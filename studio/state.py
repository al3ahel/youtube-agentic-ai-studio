"""
Shared run state for the studio: one video is worked on at a time, so the
pipeline thread and every route read and write this single dict.
Also a bounded event journal: every browser receives its own copy of a run's
events. The queue-compatible producer API remains for existing pipeline code.
"""
import queue
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone


_event_context = ContextVar("studio_event_context", default=None)
_event_stage = ContextVar("studio_event_stage", default=None)


@contextmanager
def event_context(*, run_id=None, profile_id=None, profile_name=None, build=None):
    """Attach a captured job identity to events, never live global selection.

    Build snapshots deliberately exclude diagnostic paths, process IDs, settings
    and credentials. A missing snapshot remains unknown (None).
    """
    def text(value, limit):
        return "".join(c for c in str(value) if ord(c) >= 32)[:limit] if value is not None else None
    safe_build = None
    if isinstance(build, dict):
        safe_build = {key: text(build[key], 128) for key in ("commit", "short_commit", "code_digest", "started_at")
                      if build.get(key) is not None}
        if isinstance(build.get("runtime_modified"), bool):
            safe_build["runtime_modified"] = build["runtime_modified"]
    metadata = {"run_id": text(run_id or uuid.uuid4().hex, 128),
                "profile_id": text(profile_id, 128), "profile_name": text(profile_name, 200),
                "build": safe_build}
    token = _event_context.set(metadata)
    stage_token = _event_stage.set(None)
    try:
        yield deepcopy(metadata)
    finally:
        _event_stage.reset(stage_token)
        _event_context.reset(token)


class EventJournal:
    """Bounded broadcast history plus a separate legacy queue view.

    SSE readers use read_after(), so one subscriber cannot steal another's
    messages. get()/get_nowait() remain compatible with old queue consumers;
    draining that view never removes the broadcast history.
    """
    def __init__(self, max_events=1000):
        if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
            raise ValueError("Event history needs a positive limit.")
        self._history = deque(maxlen=max_events)
        self._legacy = deque(maxlen=max_events)
        self._condition = threading.Condition()
        self._sequence = 0
        self.epoch = uuid.uuid4().hex

    def event_id(self, sequence):
        return f"{self.epoch}:{sequence}"

    def cursor(self, value=None):
        """Resume an opaque SSE ID, or begin at retained history's first row."""
        with self._condition:
            first = self._history[0]["sequence"] - 1 if self._history else self._sequence
            if not value:
                return first
            if not isinstance(value, str) or len(value) > 160:
                raise ValueError("Invalid event cursor.")
            epoch, separator, sequence = value.rpartition(":")
            if (not separator or len(epoch) != 32 or any(char not in "0123456789abcdef" for char in epoch)
                    or not sequence.isascii() or not sequence.isdigit()):
                raise ValueError("Invalid event cursor.")
            # A browser reconnecting to a new process must not wait for the old
            # process's sequence number to be reached again.
            if epoch != self.epoch:
                return first
            return min(int(sequence), self._sequence)

    def put(self, item, block=True, timeout=None):
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError("An event needs a kind and message.")
        kind, message = str(item[0]), str(item[1])
        message = message if len(message) <= 16384 else message[:16360] + "… [message truncated]"
        metadata = deepcopy(_event_context.get() or {"run_id": None, "profile_id": None, "profile_name": None, "build": None})
        with self._condition:
            self._sequence += 1
            event = {"kind": kind, "msg": message, "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                     "sequence": self._sequence, "id": self.event_id(self._sequence),
                     "stage": _event_stage.get(), **metadata}
            self._history.append(event)
            self._legacy.append((kind, message))
            self._condition.notify_all()

    put_nowait = put

    def read_after(self, sequence, *, timeout=30, cancel_event=None):
        deadline = time.monotonic() + max(0, timeout)
        with self._condition:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    return [], False
                first = self._history[0]["sequence"] if self._history else self._sequence + 1
                events = [deepcopy(event) for event in self._history if event["sequence"] > sequence]
                if events:
                    return events, sequence < first - 1
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return [], False
                self._condition.wait(min(remaining, .2) if cancel_event is not None else remaining)

    def get(self, block=True, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._legacy:
                if not block:
                    raise queue.Empty
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise queue.Empty
                self._condition.wait(remaining)
            return self._legacy.popleft()

    def get_nowait(self):
        return self.get(block=False)

    def empty(self):
        with self._condition:
            return not self._legacy

    def qsize(self):
        with self._condition:
            return len(self._legacy)

log_queue = EventJournal()
stop_event = threading.Event()   # set() to request pipeline stop
job_lock = threading.RLock()     # reserve a run or a storyboard edit atomically

state = {
    "running":        False,
    "stop_requested": False,
    "video_type":     "normal",
    "visual_mode":    None,
    "preview_only":   False,
    "visual_editing": False,
    "workspace_warning": None,
    "workspace_restored": False,
    "stages": {
        "preparation": "pending",
        "research":  "pending",   # pending | running | done | error
        "script":    "pending",
        "images":    "pending",
        "narration": "pending",
        "visuals":   "pending",
        "video":     "pending",
    },
    "video_path":   None,
    "preview_path": None,
    "visual_plan":  None,
    "timeline":     None,
    "visual_qa":    None,
    "preflight":    None,
    "thumb_path":   None,
    "thumb_bg":     None,         # explicit thumbnail background (AI art / library cover)
    "thumb_bg_index": 0,          # which video photo the thumbnail uses
    "thumb_style":  None,         # None → config.THUMB_STYLE
    "thumb_text":   None,         # None → auto from the title
    "audio_path":   None,
    "render_audio_path": None,   # soundtrack chosen for this project's render
    "image_map":    {},
    "image_reuse_note": None,
    "script":       None,
    "research":     None,
    "yt_url":       None,
    "error":        None,
    "upload_pct":   0,
    "uploading":    False,
    "upload_warning": None,       # e.g. thumbnail refused; the video itself uploaded
    "library_id":   None,         # set once the run is archived to the Library
    "started_at":   None,
    "profile_id":   None,
    "profile_name": None,
    "job_profile_id": None,
    "job_profile_name": None,
    "job_profile_values": None,
    "run_id": None,
    "schedule_id": None,
    "job_output_dir": None,
}

_EMPTY_PROJECT = deepcopy(state)


def fresh_project_state():
    return deepcopy(_EMPTY_PROJECT)

class Capture:
    """Capture this worker's prints, not unrelated requests in other threads."""
    def __init__(self, orig):
        self._orig = orig
        self._owner = threading.get_ident()
    def write(self, t):
        if t.strip() and threading.get_ident() == self._owner:
            log_queue.put(("log", t.rstrip()))
        try: self._orig.write(t)
        except UnicodeEncodeError: self._orig.write(t.encode("ascii", "replace").decode())
    def flush(self): self._orig.flush()

def log(msg, level="info"):
    log_queue.put(("log", f"[{level.upper()}] {msg}"))

def stage(name, status):
    state["stages"][name] = status
    _event_stage.set(name)
    log_queue.put(("stage", f"{name}:{status}"))

def is_shorts(script=None):
    script = script if script is not None else (state.get("script") or {})
    return state.get("video_type") == "shorts" or script.get("video_type") == "shorts"
