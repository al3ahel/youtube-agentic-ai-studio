"""Time zones, durable dispatch and keeping channel/editor state isolated."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading

import pytest

import config
from studio import create_app, channels, schedules, runner
from studio.state import fresh_project_state, state, stop_event

UTC = timezone.utc
NOW = datetime(2026, 11, 8, 8, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean_job(monkeypatch):
    previous = deepcopy(state)
    state.clear()
    state.update(fresh_project_state())
    monkeypatch.setattr(schedules, "utcnow", lambda: NOW)
    monkeypatch.setattr(schedules, "_service", None)
    stop_event.clear()
    yield
    state.clear()
    state.update(previous)
    stop_event.clear()


def schedule(**changes):
    return schedules.create_schedule({"profile_id": "default", "name": "Morning news",
        "topic": "Explain today's verified space news", "cron": "0 9 * * *", "timezone": "UTC",
        "enabled": True, **changes})


def test_local_time_and_dst_skip_nonexistent_hour():
    assert schedules.next_occurrence("0 9 * * *", "Asia/Dubai", NOW).isoformat() == "2026-11-09T05:00:00+00:00"
    spring = datetime(2026, 3, 8, 6, 50, tzinfo=UTC)
    assert schedules.next_occurrence("30 2 * * *", "America/New_York", spring).isoformat() == "2026-03-09T06:30:00+00:00"


@pytest.mark.parametrize("changes", [
    {"cron": "* * * * *"}, {"cron": "0,1 * * * *"}, {"cron": "0 0 31 2 *"},
    {"cron": "* * * * * *"}, {"timezone": "Not/AZone"}, {"enabled": "yes"},
    {"visual_mode": "classic", "preview_only": True}, {"command": "anything"},
    {"profile_id": "../other"}, {"topic": "x" * 2001},
    {"video_type": []}, {"visual_mode": {}},
])
def test_invalid_or_excessive_schedule_is_rejected(changes):
    with pytest.raises((ValueError, LookupError)):
        schedule(**changes)
    assert schedules.list_schedules() == []


def test_tick_is_idempotent_and_keeps_profiles_distinct():
    other = channels.create_profile("Quiet channel", preset="asmr", activate=False)
    first = schedule()
    second = schedule(profile_id=other["id"])
    due = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
    assert len(schedules.tick(due)) == 2
    assert schedules.tick(due) == []
    with schedules._db() as db:
        jobs = db.execute("SELECT * FROM schedule_jobs").fetchall()
    assert {row["profile_id"] for row in jobs} == {"default", other["id"]}
    assert len(schedules.list_schedules(other["id"])) == 1
    assert all(row["next_run_at"].startswith("2026-11-09T09:00") for row in schedules.list_schedules())


def test_missed_offline_slots_skip_without_backlog():
    item = schedule()
    assert schedules.tick(datetime(2026, 11, 10, 9, 30, tzinfo=UTC)) == []
    row = schedules.list_schedules()[0]
    assert row["last_status"] == "skipped"
    assert row["next_run_at"].startswith("2026-11-11T09:00")


def test_autumn_repeated_wall_clock_slot_runs_once():
    item = schedule(cron="30 1 * * *", timezone="America/New_York")
    with schedules._db() as db:
        db.execute("UPDATE schedules SET next_run_at=? WHERE id=?", ("2026-11-01T05:30:00+00:00", item["id"]))
    assert len(schedules.tick(datetime(2026, 11, 1, 5, 30, tzinfo=UTC))) == 1
    with schedules._db() as db:
        db.execute("UPDATE schedule_jobs SET status='completed'")
        db.execute("UPDATE schedules SET next_run_at=? WHERE id=?", ("2026-11-01T06:30:00+00:00", item["id"]))
    assert schedules.tick(datetime(2026, 11, 1, 6, 30, tzinfo=UTC)) == []
    with schedules._db() as db:
        assert db.execute("SELECT COUNT(*) FROM schedule_jobs").fetchone()[0] == 1


def test_edit_cancels_obsolete_queued_job_and_disabled_schedule_stays_disabled():
    item = schedule()
    schedules.enqueue_now(item["id"])
    updated = schedules.update_schedule(item["id"], {"enabled": False, "topic": "New topic"})
    assert updated["enabled"] is False and updated["next_run_at"] is None
    with schedules._db() as db:
        assert db.execute("SELECT status FROM schedule_jobs").fetchone()[0] == "skipped"


def test_busy_editor_defers_without_consuming_job():
    item = schedule()
    schedules.enqueue_now(item["id"])
    state["visual_editing"] = True
    assert schedules.execute_next() is False
    with schedules._db() as db:
        assert db.execute("SELECT status FROM schedule_jobs").fetchone()[0] == "queued"


def test_background_job_restores_open_editor_and_archives_under_its_profile(monkeypatch):
    other = channels.create_profile("ASMR", preset="asmr", activate=False)
    item = schedule(profile_id=other["id"])
    schedules.enqueue_now(item["id"])
    state.update(script={"title": "Manual work"}, profile_id="default", profile_name="My channel",
                 audio_path="original.wav", image_map={1: ["original.jpg"]})
    before = deepcopy(state)
    called = []
    def run(steps, *args, **kwargs):
        called.append(kwargs)
        assert state["running"] and state["profile_id"] == other["id"]
        assert state.get("script") is None
        assert kwargs["release_job"] is False
        assert kwargs["preview_only"] is True
        assert kwargs["profile_values"]["VOICE_RATE"] == "-15%"
        assert Path(kwargs["profile_values"]["OUTPUT_DIR"]).is_relative_to(Path(config.DATA_DIR))
        state["library_id"] = "saved-draft"
    monkeypatch.setattr(runner, "run_pipeline_thread", run)
    assert schedules.execute_next() is True
    assert state == before
    assert schedules.list_schedules(other["id"])[0]["last_library_id"] == "saved-draft"
    assert channels.active_profile_id() == "default"
    assert len(called) == 1
    assert schedules.execute_next() is False


def test_failure_preserves_manual_editor_and_is_not_retried(monkeypatch):
    item = schedule()
    schedules.enqueue_now(item["id"])
    before = deepcopy(state)
    monkeypatch.setattr(runner, "run_pipeline_thread", lambda *a, **kw: state.update(error="Provider unavailable"))
    assert schedules.execute_next() is True
    assert state == before
    assert schedules.list_schedules()[0]["last_status"] == "failed"
    assert schedules.execute_next() is False


def test_api_crud_scope_and_duplicate_run_now():
    client = create_app().test_client()
    item = client.post("/api/schedules", json={"profile_id": "default", "name": "Draft", "cron": "0 9 * * *"}).json["schedule"]
    assert client.post(f"/api/schedules/{item['id']}/run", json={}).status_code == 202
    assert client.post(f"/api/schedules/{item['id']}/run", json={}).status_code == 409
    assert client.get("/api/schedules?profile_id=default").json["scheduler"]["requires_app_running"]
    assert client.delete(f"/api/schedules/{item['id']}").status_code == 200
    assert client.get("/api/schedules?profile_id=default").json["schedules"] == []


def test_profile_context_cannot_leak_between_threads():
    base = config.CHANNEL_NAME
    barrier = threading.Barrier(2)
    seen = []
    def render(name):
        with config.profile_context({"CHANNEL_NAME": name, "BRAND_TEXT": "", "SHOW_CHANNEL_BRAND": True}):
            barrier.wait(timeout=5)
            seen.append((config.CHANNEL_NAME, config.brand_text()))
    threads = [threading.Thread(target=render, args=(name,)) for name in ("News", "ASMR")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(seen) == [("ASMR", "ASMR"), ("News", "News")]
    assert config.CHANNEL_NAME == base


def test_default_context_does_not_inherit_another_channel():
    base = config.CHANNEL_NAME
    with config.profile_context({"CHANNEL_NAME": "ASMR"}):
        with config.profile_context({}):
            assert config.CHANNEL_NAME == base
        assert config.CHANNEL_NAME == "ASMR"


def test_expired_automatic_queue_is_skipped_after_restart(monkeypatch):
    schedule()
    schedules.tick(datetime(2026, 11, 8, 9, 0, tzinfo=UTC))
    monkeypatch.setattr(schedules, "utcnow", lambda: datetime(2026, 11, 8, 10, 0, tzinfo=UTC))
    monkeypatch.setattr(runner, "run_pipeline_thread", lambda *a, **kw: pytest.fail("Expired job must not run"))
    assert schedules.execute_next() is True
    assert schedules.list_schedules()[0]["last_status"] == "skipped"


def test_scheduled_media_and_status_follow_captured_job_not_selected_channel(tmp_path, monkeypatch):
    other = channels.create_profile("Quiet channel", preset="asmr", activate=False)
    manual, job = tmp_path / "manual", tmp_path / "job"
    manual.mkdir()
    job.mkdir()
    (manual / "narration.srt").write_text("Manual channel captions", encoding="utf-8")
    (job / "narration.srt").write_text("Scheduled channel captions", encoding="utf-8")
    monkeypatch.setattr(config, "OUTPUT_DIR", str(manual))
    state.update(running=True, profile_id=other["id"], job_profile_id=other["id"],
                 job_profile_name="Quiet channel", job_output_dir=str(job),
                 job_profile_values={"CHANNEL_NAME": "Captured quiet brand", "OUTPUT_DIR": str(job)})
    client = create_app().test_client()
    response = client.get("/captions")
    assert response.data == b"Scheduled channel captions"
    response.close()
    status = client.get("/api/status").json
    assert status["channel_name"] == "Captured quiet brand"
    assert status["profile_id"] == other["id"] and status["active_profile_id"] == "default"
    assert client.get("/captions?profile_id=default").status_code == 400


def test_only_one_scheduler_process_can_own_the_queue(monkeypatch):
    monkeypatch.setattr(schedules.Scheduler, "_loop", lambda self: self.stop.wait(5))
    monkeypatch.setattr(schedules.Scheduler, "_work", lambda self: self.stop.wait(5))
    first, second = schedules.Scheduler(), schedules.Scheduler()
    try:
        assert first.start() is True
        assert second.start() is False
        assert "Another studio process" in second.error
    finally:
        first.close()
        second.close()


def test_queue_connections_close_and_failed_transactions_roll_back():
    with schedules._db() as committed:
        committed.execute("CREATE TABLE connection_check(value INTEGER)")
        committed.execute("INSERT INTO connection_check VALUES(1)")
    with pytest.raises(schedules.sqlite3.ProgrammingError, match="closed"):
        committed.execute("SELECT 1")
    with pytest.raises(RuntimeError, match="interrupted write"):
        with schedules._db() as failed:
            failed.execute("INSERT INTO connection_check VALUES(2)")
            raise RuntimeError("interrupted write")
    with pytest.raises(schedules.sqlite3.ProgrammingError, match="closed"):
        failed.execute("SELECT 1")
    with schedules._db() as restored:
        assert [row[0] for row in restored.execute("SELECT value FROM connection_check")] == [1]
