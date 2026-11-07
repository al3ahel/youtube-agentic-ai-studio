"""Persistent, per-channel generation schedules. No shell cron or automatic upload.

One scheduler process owns a file lock; a durable SQLite queue serializes jobs
with manual renders. Each job uses its own working directory and restores the
open editor after archiving, including drafts. Missed offline slots are skipped.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, jsonify, request

import config
from studio.state import job_lock, state, stop_event

bp = Blueprint("schedules", __name__)
UTC = timezone.utc
MIN_INTERVAL = 15 * 60
MISFIRE_GRACE = 15 * 60
_service = None
_service_lock = threading.Lock()


def _serialized(function):
    """All mutations acquire the job lock before the DB and profile registry."""
    @wraps(function)
    def run(*args, **kwargs):
        with job_lock:
            return function(*args, **kwargs)
    return run


def utcnow():
    return datetime.now(UTC)


def stamp(value):
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _date(value):
    return datetime.fromisoformat(value).astimezone(UTC)


def next_occurrence(expression, zone, after=None):
    from croniter import croniter, CroniterBadCronError, CroniterBadDateError
    after = after or utcnow()
    if after.tzinfo is None:
        raise ValueError("Scheduling needs a timezone-aware clock.")
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError("Choose an IANA time zone, for example Asia/Dubai or America/New_York.") from exc
    if not isinstance(expression, str) or len(expression) > 100 or len(expression.split()) != 5:
        raise ValueError("Use a five-field cron expression: minute hour day month weekday.")
    try:
        iterator = croniter(expression, after.astimezone(tz), max_years_between_matches=5)
        # A nonexistent local time at the spring clock change is skipped. Do not
        # turn an intended 02:30 slot into an unrelated 03:30 slot.
        for _ in range(8):
            candidate = iterator.get_next(datetime)
            absolute = candidate.astimezone(UTC)
            local = absolute.astimezone(tz)
            if absolute > after.astimezone(UTC) and croniter.match(expression, local.replace(tzinfo=None)):
                return absolute
    except (CroniterBadCronError, CroniterBadDateError, KeyError, OverflowError) as exc:
        raise ValueError("That cron expression has no valid occurrence in the next five years.") from exc
    raise ValueError("Could not find a valid future run for that cron expression.")


def _validate(body, previous=None):
    from studio.channels import get_profile
    if not isinstance(body, dict):
        raise ValueError("Expected schedule settings as an object.")
    allowed = {"profile_id", "name", "topic", "cron", "timezone", "video_type",
               "visual_mode", "preview_only", "enabled", "use_trends"}
    unknown = set(body) - allowed
    if unknown:
        raise ValueError("Unknown schedule setting: " + ", ".join(sorted(unknown)))
    settings = {"name": "Scheduled video", "topic": "", "cron": "0 9 * * *",
                "timezone": "UTC", "video_type": "normal", "visual_mode": "motion",
                "preview_only": True, "enabled": False, "use_trends": False}
    settings.update(previous or {})
    settings.update(body)
    profile = get_profile(settings.get("profile_id"))
    settings["profile_id"] = profile["id"]
    for name, maximum, required in (("name", 100, True), ("topic", 2000, False)):
        value = settings[name]
        if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
            raise ValueError(f"{name.capitalize()} must be text of at most {maximum} characters.")
        settings[name] = value.strip()
    for name in ("preview_only", "enabled", "use_trends"):
        if not isinstance(settings[name], bool):
            raise ValueError(f"{name} must be true or false.")
    if not isinstance(settings["video_type"], str) or settings["video_type"] not in {"normal", "shorts"}:
        raise ValueError("Choose a long video or Short.")
    if not isinstance(settings["visual_mode"], str) or settings["visual_mode"] not in {"motion", "hybrid", "classic"}:
        raise ValueError("Choose Motion, Hybrid or Classic.")
    if settings["preview_only"] and settings["visual_mode"] == "classic":
        raise ValueError("Classic generates a final local video. Draft MP4s use Motion or Hybrid.")
    first = next_occurrence(settings["cron"], settings["timezone"])
    previous_at = first
    for _ in range(16):
        following = next_occurrence(settings["cron"], settings["timezone"], previous_at)
        if (following - previous_at).total_seconds() < MIN_INTERVAL:
            raise ValueError("Leave at least 15 minutes between scheduled videos.")
        previous_at = following
    return {key: settings[key] for key in allowed}, first


@contextmanager
def _db():
    root = Path(config.DATA_DIR)
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "schedules.sqlite3", timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS schedules (
          id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, settings TEXT NOT NULL,
          enabled INTEGER NOT NULL, next_run_at TEXT, created_at TEXT NOT NULL,
          last_run_at TEXT, last_status TEXT, last_error TEXT, last_library_id TEXT
        );
        CREATE TABLE IF NOT EXISTS schedule_jobs (
          id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, profile_id TEXT NOT NULL,
          scheduled_for TEXT NOT NULL, created_at TEXT NOT NULL,
          payload TEXT NOT NULL, status TEXT NOT NULL,
          finished_at TEXT, error TEXT, library_id TEXT,
          UNIQUE(schedule_id, scheduled_for),
          FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS schedule_queue ON schedule_jobs(status, created_at);
        """)
        # sqlite3's connection context commits/rolls back but does not close.
        # The always-on scheduler must release each connection deterministically.
        with connection:
            yield connection
    finally:
        connection.close()


def _public(row):
    result = dict(row)
    enabled = bool(result["enabled"])
    settings = json.loads(result.pop("settings"))
    result.update(settings)
    result["enabled"] = enabled
    return result


def list_schedules(profile_id=None):
    with _db() as db:
        rows = db.execute("SELECT * FROM schedules WHERE profile_id=? ORDER BY created_at", (profile_id,)).fetchall() if profile_id else db.execute("SELECT * FROM schedules ORDER BY created_at").fetchall()
        return [_public(row) for row in rows]


@_serialized
def create_schedule(body):
    settings, next_run = _validate(body)
    identifier = uuid.uuid4().hex
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM schedules").fetchone()[0] >= 64:
            raise ValueError("The studio supports up to 64 schedules.")
        db.execute("INSERT INTO schedules(id,profile_id,settings,enabled,next_run_at,created_at) VALUES(?,?,?,?,?,?)",
                   (identifier, settings["profile_id"], json.dumps(settings), int(settings["enabled"]),
                    stamp(next_run) if settings["enabled"] else None, stamp(utcnow())))
        return _public(db.execute("SELECT * FROM schedules WHERE id=?", (identifier,)).fetchone())


@_serialized
def update_schedule(identifier, body):
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM schedules WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise LookupError("Schedule not found.")
        if db.execute("SELECT 1 FROM schedule_jobs WHERE schedule_id=? AND status='running'", (identifier,)).fetchone():
            raise RuntimeError("Wait for this schedule's current video to finish.")
        settings, next_run = _validate(body, json.loads(row["settings"]))
        db.execute("UPDATE schedules SET profile_id=?,settings=?,enabled=?,next_run_at=? WHERE id=?",
                   (settings["profile_id"], json.dumps(settings), int(settings["enabled"]),
                    stamp(next_run) if settings["enabled"] else None, identifier))
        # An edited/disabled schedule must not retain a queued obsolete job.
        db.execute("UPDATE schedule_jobs SET status='skipped',error=?,finished_at=? WHERE schedule_id=? AND status='queued'",
                   ("Schedule changed before generation started.", stamp(utcnow()), identifier))
        return _public(db.execute("SELECT * FROM schedules WHERE id=?", (identifier,)).fetchone())


@_serialized
def delete_schedule(identifier):
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM schedule_jobs WHERE schedule_id=? AND status='running'", (identifier,)).fetchone():
            raise RuntimeError("Wait for this schedule's current video to finish.")
        if not db.execute("DELETE FROM schedules WHERE id=?", (identifier,)).rowcount:
            raise LookupError("Schedule not found.")


def _enqueue(db, row, scheduled_for, now):
    from studio.channels import get_profile, config_overrides, DEFAULT_CONFIG_MAP
    if db.execute("SELECT 1 FROM schedule_jobs WHERE schedule_id=? AND status IN ('queued','running')", (row["id"],)).fetchone():
        return None
    if db.execute("SELECT COUNT(*) FROM schedule_jobs WHERE status='queued'").fetchone()[0] >= 128:
        raise RuntimeError("The generation queue is full.")
    profile = get_profile(row["profile_id"])
    settings = json.loads(row["settings"])
    values = {attr: deepcopy(vars(config)[attr]) for attr in DEFAULT_CONFIG_MAP.values() if attr in vars(config)}
    values.update(config_overrides(profile["id"]))
    payload = {**settings, "profile_name": profile["name"], "profile_values": values}
    identifier = uuid.uuid4().hex
    result = db.execute("INSERT OR IGNORE INTO schedule_jobs(id,schedule_id,profile_id,scheduled_for,created_at,payload,status) VALUES(?,?,?,?,?,?,'queued')",
                        (identifier, row["id"], row["profile_id"], scheduled_for, stamp(now), json.dumps(payload)))
    if not result.rowcount:
        return None
    db.execute("UPDATE schedules SET last_status='queued',last_error=NULL WHERE id=?", (row["id"],))
    return identifier


@_serialized
def enqueue_now(identifier):
    now = utcnow()
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM schedules WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise LookupError("Schedule not found.")
        job_id = _enqueue(db, row, "manual:" + uuid.uuid4().hex, now)
        if not job_id:
            raise RuntimeError("This schedule already has a queued or running video.")
        return job_id


@_serialized
def tick(now=None):
    """Advance due slots and enqueue each once, even after repeated ticks/restarts."""
    now = now or utcnow()
    queued = []
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at,id", (stamp(now),)).fetchall()
        for row in rows:
            settings = json.loads(row["settings"])
            try:
                next_run = stamp(next_occurrence(settings["cron"], settings["timezone"], now))
                previous = _date(row["last_run_at"]) if row["last_run_at"] else None
                if (now - _date(row["next_run_at"])).total_seconds() > MISFIRE_GRACE:
                    raise RuntimeError("Missed while the studio was offline; the next scheduled slot is retained.")
                if previous and (now - previous).total_seconds() < MIN_INTERVAL:
                    raise RuntimeError("Skipped to keep at least 15 minutes between videos.")
                local_slot = _date(row["next_run_at"]).astimezone(ZoneInfo(settings["timezone"]))
                # Run a repeated autumn wall-clock minute once, even though it
                # has two possible UTC instants.
                slot_key = "cron:" + settings["timezone"] + ":" + local_slot.strftime("%Y-%m-%dT%H:%M")
                job_id = _enqueue(db, row, slot_key, now)
                if job_id:
                    queued.append(job_id)
            except (ValueError, LookupError, RuntimeError) as exc:
                # Invalid/deleted profiles disable the schedule, while missed
                # offline slots simply advance to the next future occurrence.
                if isinstance(exc, (ValueError, LookupError)):
                    db.execute("UPDATE schedules SET enabled=0,next_run_at=NULL,last_status='failed',last_error=? WHERE id=?", (str(exc)[:500], row["id"]))
                    continue
                db.execute("UPDATE schedules SET last_status='skipped',last_error=? WHERE id=?", (str(exc)[:500], row["id"]))
            db.execute("UPDATE schedules SET next_run_at=? WHERE id=?", (next_run, row["id"]))
    return queued


def _finish(job, status, error=None, library_id=None):
    now = stamp(utcnow())
    with _db() as db:
        db.execute("UPDATE schedule_jobs SET status=?,finished_at=?,error=?,library_id=? WHERE id=?",
                   (status, now, error, library_id, job["id"]))
        db.execute("UPDATE schedules SET last_status=?,last_error=?,last_library_id=? WHERE id=?",
                   (status, error, library_id, job["schedule_id"]))
        db.execute("DELETE FROM schedule_jobs WHERE id IN (SELECT id FROM schedule_jobs WHERE status NOT IN ('queued','running') ORDER BY created_at DESC LIMIT -1 OFFSET 500)")


def execute_next():
    """Run one queued job in isolation, without replacing the user's open draft."""
    from studio.channels import get_profile, is_connecting
    from studio.pipeline import DEFAULT_STEPS
    from studio.runner import run_pipeline_thread
    with job_lock:
        if state.get("running") or state.get("uploading") or state.get("visual_editing") or is_connecting():
            return False
        with _db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM schedule_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                return False
            job = dict(row)
            if job["scheduled_for"].startswith("cron:") and (utcnow() - _date(job["created_at"])).total_seconds() > MISFIRE_GRACE:
                error = "The scheduled slot expired while the studio was offline or busy. Use Run now to request a fresh video."
                db.execute("UPDATE schedule_jobs SET status='skipped',error=?,finished_at=? WHERE id=?", (error, stamp(utcnow()), job["id"]))
                db.execute("UPDATE schedules SET last_status='skipped',last_error=? WHERE id=?", (error, job["schedule_id"]))
                return True
            db.execute("UPDATE schedule_jobs SET status='running' WHERE id=?", (job["id"],))
            db.execute("UPDATE schedules SET last_status='running',last_error=NULL,last_run_at=? WHERE id=?", (stamp(utcnow()), job["schedule_id"]))
        previous_state = deepcopy(state)
        from studio.state import fresh_project_state
        state.clear()
        state.update(fresh_project_state())
        state.update(running=True, schedule_id=job["schedule_id"], job_profile_id=job["profile_id"])
        stop_event.clear()
    folder = Path(config.DATA_DIR).resolve() / "jobs" / job["id"]
    succeeded = False
    try:
        profile = get_profile(job["profile_id"])
        payload = json.loads(job["payload"])
        values = {**payload.get("profile_values", {}), "OUTPUT_DIR": str(folder), "IMAGES_DIR": str(folder / "images")}
        state.update(profile_id=profile["id"], profile_name=payload.get("profile_name", profile["name"]),
                     job_profile_name=payload.get("profile_name", profile["name"]), job_output_dir=str(folder))
        run_pipeline_thread(DEFAULT_STEPS, payload["topic"], payload["video_type"], "", payload.get("use_trends", False),
                            visual_mode=payload["visual_mode"], preview_only=payload["preview_only"],
                            profile_id=profile["id"], profile_values=values,
                            schedule_id=job["schedule_id"], release_job=False)
        if state.get("error"):
            _finish(job, "failed", str(state["error"])[:500])
        elif stop_event.is_set():
            _finish(job, "failed", "Generation was stopped.")
        elif not state.get("library_id"):
            _finish(job, "failed", "The generated project could not be archived; inspect the local job files.")
        else:
            _finish(job, "completed", library_id=state["library_id"])
            succeeded = True
    except Exception as exc:
        _finish(job, "failed", str(exc)[:500])
    finally:
        with job_lock:
            state.clear()
            state.update(previous_state)
            stop_event.clear()
        if succeeded:
            shutil.rmtree(folder, ignore_errors=True)
    return True


class Scheduler:
    def __init__(self):
        self.stop = threading.Event()
        self.last_tick = None
        self.error = None
        self.thread = None
        self.worker = None
        self.lock_file = None

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive() and not self.stop.is_set())

    def start(self):
        root = Path(config.DATA_DIR)
        root.mkdir(parents=True, exist_ok=True)
        handle = open(root / "scheduler.lock", "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            self.error = "Another studio process owns the scheduler. Use one running studio."
            return False
        self.lock_file = handle
        with _db() as db:
            interrupted = db.execute("SELECT id,schedule_id FROM schedule_jobs WHERE status='running'").fetchall()
            for row in interrupted:
                db.execute("UPDATE schedule_jobs SET status='failed',error=?,finished_at=? WHERE id=?",
                           ("The previous studio process stopped during generation.", stamp(utcnow()), row["id"]))
                db.execute("UPDATE schedules SET last_status='failed',last_error=? WHERE id=?",
                           ("The previous studio process stopped during generation; it was not retried automatically.", row["schedule_id"]))
        self.thread = threading.Thread(target=self._loop, name="yaas-scheduler", daemon=True)
        self.worker = threading.Thread(target=self._work, name="yaas-scheduled-videos", daemon=True)
        self.thread.start()
        self.worker.start()
        return True

    def _loop(self):
        while not self.stop.is_set():
            try:
                tick()
                self.last_tick = stamp(utcnow())
                self.error = None
            except Exception as exc:
                self.error = str(exc)[:500]
            self.stop.wait(15)

    def _work(self):
        while not self.stop.is_set():
            try:
                worked = execute_next()
            except Exception as exc:
                self.error = str(exc)[:500]
                worked = False
            self.stop.wait(1 if worked else 3)

    def close(self):
        self.stop.set()
        if state.get("schedule_id") and state.get("running"):
            stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        # A running renderer is allowed to finish while the app shuts down.
        # Keep the OS lock until the worker exits or process termination.
        if self.worker:
            self.worker.join(timeout=2)
        if self.lock_file and not (self.worker and self.worker.is_alive()):
            self.lock_file.close()
            self.lock_file = None


def start_scheduler():
    global _service
    with _service_lock:
        if _service and _service.running:
            return _service
        _service = Scheduler()
        _service.start()
        return _service


def scheduler_status():
    return {"running": bool(_service and _service.running), "last_tick": _service.last_tick if _service else None,
            "error": _service.error if _service else None, "requires_app_running": True,
            "minimum_interval_minutes": MIN_INTERVAL // 60}


def _error(exc):
    return jsonify({"ok": False, "error": str(exc)}), 404 if isinstance(exc, LookupError) else 409 if isinstance(exc, RuntimeError) else 400


@bp.route("/api/schedules", methods=["GET", "POST"])
def schedules_route():
    try:
        if request.method == "POST":
            return jsonify({"ok": True, "schedule": create_schedule(request.get_json())}), 201
        profile_id = request.args.get("profile_id")
        if profile_id:
            from studio.channels import get_profile
            profile_id = get_profile(profile_id)["id"]
        return jsonify({"schedules": list_schedules(profile_id), "scheduler": scheduler_status()})
    except (ValueError, LookupError, RuntimeError) as exc:
        return _error(exc)


@bp.route("/api/schedules/<identifier>", methods=["PATCH", "DELETE"])
def schedule_route(identifier):
    try:
        if request.method == "DELETE":
            delete_schedule(identifier)
            return jsonify({"ok": True})
        return jsonify({"ok": True, "schedule": update_schedule(identifier, request.get_json())})
    except (ValueError, LookupError, RuntimeError) as exc:
        return _error(exc)


@bp.route("/api/schedules/<identifier>/run", methods=["POST"])
def run_schedule(identifier):
    try:
        return jsonify({"ok": True, "job_id": enqueue_now(identifier), "status": "queued"}), 202
    except (ValueError, LookupError, RuntimeError) as exc:
        return _error(exc)
