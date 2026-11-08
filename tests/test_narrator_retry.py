from types import SimpleNamespace

from edge_tts.exceptions import NoAudioReceived
import pytest

from video import narrator


def test_empty_stream_retries_fresh_files_and_records_attempts(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ("audio.mp3", "captions.srt", "timings.json")]
    calls, delays, audit = [], [], []

    async def stream(text, *args):
        calls.append(text)
        assert not any(path.exists() for path in paths)
        for path in paths:
            path.write_bytes(b"partial" if len(calls) == 1 else b"complete")
        if len(calls) == 1:
            raise NoAudioReceived("Empty service stream")
        return 1

    monkeypatch.setattr(narrator, "_synthesise", stream)
    monkeypatch.setattr(narrator.time, "sleep", delays.append)
    assert narrator._record_section("Unchanged complete sentence", *map(str, paths),
                                    section_id="test", attempts=audit) == 1
    assert calls == ["Unchanged complete sentence"] * 2
    assert delays == [2]
    assert all(path.read_bytes() == b"complete" for path in paths)
    assert [row["status"] for row in audit] == ["no_audio_received", "recorded"]


def test_empty_stream_retries_are_bounded(tmp_path, monkeypatch):
    audit, delays = [], []

    async def empty(*args):
        raise NoAudioReceived("Empty service stream")

    monkeypatch.setattr(narrator, "_synthesise", empty)
    monkeypatch.setattr(narrator.time, "sleep", delays.append)
    with pytest.raises(NoAudioReceived):
        narrator._record_section("text", *(str(tmp_path / name) for name in ("a.mp3", "a.srt", "a.json")), attempts=audit)
    assert len(audit) == 3
    assert delays == [2, 5]


def test_nonempty_service_errors_are_not_retried(tmp_path, monkeypatch):
    audit = []

    async def rejected(*args):
        raise RuntimeError("403 rejected")

    monkeypatch.setattr(narrator, "_synthesise", rejected)
    monkeypatch.setattr(narrator.time, "sleep", lambda _: pytest.fail("An authentication error must not retry"))
    with pytest.raises(RuntimeError, match="403"):
        narrator._record_section("text", *(str(tmp_path / name) for name in ("a.mp3", "a.srt", "a.json")), attempts=audit)
    assert len(audit) == 1
    assert audit[0]["status"] == "failed"


def test_retry_backoff_is_cancellable(tmp_path, monkeypatch):
    audit = []

    async def empty(*args):
        raise NoAudioReceived("Empty service stream")

    monkeypatch.setattr(narrator, "_synthesise", empty)
    cancelled = SimpleNamespace(is_set=lambda: False, wait=lambda delay: True)
    with pytest.raises(InterruptedError, match="retry"):
        narrator._record_section("text", *(str(tmp_path / name) for name in ("a.mp3", "a.srt", "a.json")),
                                 stop_event=cancelled, attempts=audit)
    assert len(audit) == 1
