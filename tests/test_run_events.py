"""Real stream-route regressions: every tab sees the same captured job events."""
from copy import deepcopy
from datetime import datetime, timezone
from io import StringIO
import json
import queue
import threading
import time

from flask import Flask
import pytest

from studio import pipeline
import studio.state as run_state
from studio.state import Capture, EventJournal, event_context, fresh_project_state, state, stop_event


@pytest.fixture
def stream_app(monkeypatch):
    previous = deepcopy(state)
    stopped = stop_event.is_set()
    state.clear()
    state.update(fresh_project_state())
    state.update(running=True, run_id="run-a", profile_id="channel-a", profile_name="News",
                 job_profile_id="channel-a", job_profile_name="News")
    journal = EventJournal(max_events=20)
    monkeypatch.setattr(run_state, "log_queue", journal)
    monkeypatch.setattr(pipeline, "log_queue", journal)
    app = Flask(__name__)
    app.config.update(TESTING=True, STUDIO_STREAM_HEARTBEAT_SECONDS=.01)
    app.register_blueprint(pipeline.bp)
    yield app, journal
    state.clear()
    state.update(previous)
    stop_event.set() if stopped else stop_event.clear()


def emit(journal, kind, message="", *, run_id="run-a", profile_id="channel-a"):
    with event_context(run_id=run_id, profile_id=profile_id, profile_name="News"):
        journal.put((kind, message))


def event(chunk):
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    return json.loads(next(line[6:] for line in text.splitlines() if line.startswith("data: ")))


def events(response):
    return [event(chunk) for chunk in response.response]


def test_two_live_listeners_both_receive_every_narration_section(stream_app):
    app, journal = stream_app
    emit(journal, "log", "Recording section 1/3")
    tab_a = app.test_client().get("/api/stream?run_id=run-a&profile_id=channel-a", buffered=False)
    tab_b = app.test_client().get("/api/stream?run_id=run-a&profile_id=channel-a", buffered=False)
    try:
        a = [event(next(tab_a.response))]
        b = [event(next(tab_b.response))]
        emit(journal, "log", "Recording section 2/3")
        emit(journal, "log", "Recording section 3/3")
        emit(journal, "done")
        a.extend(events(tab_a))
        b.extend(events(tab_b))
        assert a == b
        assert [item["msg"] for item in a] == ["Recording section 1/3", "Recording section 2/3", "Recording section 3/3", ""]
        assert len({item["id"] for item in a}) == 4
        assert tab_a.headers["Cache-Control"] == "no-store"
        assert tab_a.headers["X-Accel-Buffering"] == "no"
    finally:
        tab_a.close()
        tab_b.close()


def test_reconnect_resumes_after_last_id_without_duplicate_lines(stream_app):
    app, journal = stream_app
    for number in range(1, 4):
        emit(journal, "log", f"section {number}")
    emit(journal, "done")
    first = app.test_client().get("/api/stream", buffered=False)
    first_event = event(next(first.response))
    first.close()
    resumed = app.test_client().get("/api/stream", headers={"Last-Event-ID": first_event["id"]})
    remaining = events(resumed)
    assert [item["msg"] for item in remaining] == ["section 2", "section 3", ""]
    query_resume = app.test_client().get("/api/stream", query_string={"after": remaining[0]["id"]})
    assert [item["msg"] for item in events(query_resume)] == ["section 3", ""]
    header_wins = app.test_client().get("/api/stream", query_string={"after": first_event["id"]},
                                      headers={"Last-Event-ID": remaining[1]["id"]})
    assert [item["kind"] for item in events(header_wins)] == ["done"]


def test_foreign_run_channel_and_unscoped_events_cannot_end_or_contaminate_stream(stream_app):
    app, journal = stream_app
    emit(journal, "error", "another channel's failure", profile_id="channel-b")
    emit(journal, "done", "old run result", run_id="run-old")
    journal.put(("log", "unscoped request output"))
    emit(journal, "log", "this run's section")
    emit(journal, "done")
    result = events(app.test_client().get("/api/stream?run_id=run-a&profile_id=channel-a"))
    assert [item["msg"] for item in result] == ["this run's section", ""]
    assert all(item["run_id"] == "run-a" and item["profile_id"] == "channel-a" for item in result)


def test_scheduled_job_scope_survives_restoration_of_other_editor(stream_app):
    app, journal = stream_app
    state.update(run_id="schedule-run", job_profile_id="scheduled-channel")
    emit(journal, "log", "Scheduled narration", run_id="schedule-run", profile_id="scheduled-channel")
    response = app.test_client().get("/api/stream", buffered=False)
    try:
        first = event(next(response.response))
        state.update(running=False, run_id="editor-run", profile_id="editor-channel", job_profile_id=None)
        emit(journal, "done", "Scheduled result", run_id="schedule-run", profile_id="scheduled-channel")
        terminal = event(next(response.response))
        assert first["run_id"] == terminal["run_id"] == "schedule-run"
        assert terminal["profile_id"] == "scheduled-channel"
        assert terminal["kind"] == "done"
        assert list(response.response) == []
    finally:
        response.close()


def test_expired_history_is_bounded_and_reconnect_reports_gap(stream_app):
    app, journal = stream_app
    for number in range(25):
        emit(journal, "log", f"line {number}")
    emit(journal, "done")
    assert journal.qsize() == 20
    retained, gap = journal.read_after(0, timeout=0)
    assert gap and len(retained) == 20
    result = events(app.test_client().get("/api/stream", headers={"Last-Event-ID": journal.event_id(1)}))
    assert result[0]["kind"] == "gap"
    assert result[0]["id"] == journal.event_id(6)
    assert result[1:] == retained


def test_old_process_cursor_starts_at_retained_history(stream_app):
    app, journal = stream_app
    emit(journal, "log", "new process")
    emit(journal, "done")
    result = events(app.test_client().get("/api/stream", headers={"Last-Event-ID": "0" * 32 + ":999999"}))
    assert [item["msg"] for item in result] == ["new process", ""]


@pytest.mark.parametrize("cursor", ["bad", ":1", "x" * 32 + ":1", "a" * 32 + ":-1", "a" * 32 + ":1.5", "a" * 32 + ":١", "a" * 180])
def test_invalid_resume_cursor_is_rejected_before_streaming(stream_app, cursor):
    app, _ = stream_app
    response = app.test_client().get("/api/stream", headers={"Last-Event-ID": cursor})
    assert response.status_code == 400
    assert "cursor" in response.json["error"]


def test_heartbeat_has_server_time_and_closing_cancels_subscription(stream_app, monkeypatch):
    app, journal = stream_app
    observed = []
    original = journal.read_after
    def record(*args, **kwargs):
        observed.append(kwargs["cancel_event"])
        return original(*args, **kwargs)
    monkeypatch.setattr(journal, "read_after", record)
    before = datetime.now(timezone.utc)
    response = app.test_client().get("/api/stream", buffered=False)
    heartbeat = event(next(response.response))
    assert heartbeat["kind"] == "ping" and "id" not in heartbeat
    assert heartbeat["run_id"] == "run-a" and heartbeat["profile_id"] == "channel-a"
    assert (datetime.fromisoformat(heartbeat["timestamp"]) - before).total_seconds() >= -.001
    response.close()
    assert observed and observed[0].is_set()


def test_stop_log_is_attached_to_reserved_job(stream_app):
    app, journal = stream_app
    response = app.test_client().post("/api/stop")
    assert response.json["ok"] and stop_event.is_set()
    rows, _ = journal.read_after(0, timeout=0)
    assert len(rows) == 1
    assert "Stop requested" in rows[0]["msg"]
    assert rows[0]["run_id"] == "run-a"
    assert rows[0]["profile_id"] == "channel-a" and rows[0]["profile_name"] == "News"


def test_legacy_queue_draining_does_not_remove_broadcast_events():
    journal = EventJournal(max_events=3)
    journal.put(("log", "legacy producer"))
    journal.put_nowait(("stage", "narration:running"))
    assert journal.get_nowait() == ("log", "legacy producer")
    assert journal.get(timeout=.01) == ("stage", "narration:running")
    assert journal.empty()
    with pytest.raises(queue.Empty):
        journal.get_nowait()
    rows, gap = journal.read_after(0, timeout=0)
    assert not gap and [row["kind"] for row in rows] == ["log", "stage"]
    assert rows[0]["run_id"] is None


def test_waiting_reader_wakes_for_publish_or_cancellation():
    journal = EventJournal()
    started = threading.Event()
    cancelled = threading.Event()
    results = []
    def read():
        started.set()
        results.append(journal.read_after(0, timeout=10, cancel_event=cancelled))
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    assert started.wait(.5)
    journal.put(("log", "wake both readers"))
    reader.join(.5)
    assert not reader.is_alive() and results[0][0][0]["msg"] == "wake both readers"
    reader = threading.Thread(target=lambda: results.append(journal.read_after(1, timeout=10, cancel_event=cancelled)), daemon=True)
    reader.start()
    cancelled.set()
    reader.join(.5)
    assert not reader.is_alive() and results[-1] == ([], False)
    start = time.monotonic()
    assert journal.read_after(1, timeout=.01) == ([], False)
    assert time.monotonic() - start < .5


def test_event_context_captures_safe_build_profile_and_stage_without_live_state(stream_app):
    _, journal = stream_app
    build = {"commit": "a" * 40, "short_commit": "a" * 8, "code_digest": "b" * 64,
             "runtime_modified": True, "project_dir": "/private/path", "pid": 123, "api_key": "secret"}
    with event_context(run_id="captured", profile_id="channel-a", profile_name="News", build=build) as context:
        run_state.stage("narration", "running")
        build["short_commit"] = "changed"
        context["profile_name"] = "changed"
        state.update(run_id="other-run", job_profile_id="channel-b")
        journal.put(("log", "section 1"))
    journal.put(("log", "outside job"))
    rows, _ = journal.read_after(0, timeout=0)
    assert rows[1]["run_id"] == "captured" and rows[1]["profile_name"] == "News"
    assert rows[1]["stage"] == "narration"
    assert rows[1]["build"] == {"commit": "a" * 40, "short_commit": "a" * 8, "code_digest": "b" * 64, "runtime_modified": True}
    assert datetime.fromisoformat(rows[1]["timestamp"]).utcoffset().total_seconds() == 0
    assert rows[2]["run_id"] is None and rows[2]["stage"] is None
    with event_context(profile_id="standalone"):
        journal.put(("done", ""))
    last, _ = journal.read_after(3, timeout=0)
    assert len(last[0]["run_id"]) == 32 and last[0]["build"] is None


def test_stdout_capture_does_not_take_unrelated_request_thread_output(stream_app):
    _, journal = stream_app
    destination = StringIO()
    capture = Capture(destination)
    with event_context(run_id="run-a", profile_id="channel-a"):
        capture.write("worker section 1\n")
        unrelated = threading.Thread(target=lambda: capture.write("another profile's request output\n"))
        unrelated.start()
        unrelated.join(.5)
        assert not unrelated.is_alive()
    rows, _ = journal.read_after(0, timeout=0)
    assert [row["msg"] for row in rows] == ["worker section 1"]
    assert "another profile's request output" in destination.getvalue()


def test_event_text_and_history_have_finite_memory_bounds():
    journal = EventJournal(max_events=2)
    for _ in range(5):
        journal.put(("log", "x" * 100000))
    rows, _ = journal.read_after(0, timeout=0)
    assert len(rows) == journal.qsize() == 2
    assert all(len(row["msg"]) <= 16384 for row in rows)
