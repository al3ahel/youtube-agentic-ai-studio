"""
Shared run state for the studio: one video is worked on at a time, so the
pipeline thread and every route read and write this single dict.
Also the live-log queue that /api/stream sends to the browser.
"""
import queue
import threading
from copy import deepcopy

log_queue  = queue.Queue()
stop_event = threading.Event()   # set() to request pipeline stop
job_lock = threading.RLock()     # reserve a run or a storyboard edit atomically

state = {
    "running":        False,
    "stop_requested": False,
    "video_type":     "normal",
    "visual_mode":    None,
    "preview_only":   False,
    "visual_editing": False,
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
    "schedule_id": None,
    "job_output_dir": None,
}

_EMPTY_PROJECT = deepcopy(state)


def fresh_project_state():
    return deepcopy(_EMPTY_PROJECT)

class Capture:
    """Redirect stdout into the log queue so pipeline prints appear in GUI."""
    def __init__(self, orig): self._orig = orig
    def write(self, t):
        if t.strip(): log_queue.put(("log", t.rstrip()))
        try: self._orig.write(t)
        except UnicodeEncodeError: self._orig.write(t.encode("ascii", "replace").decode())
    def flush(self): self._orig.flush()

def log(msg, level="info"):
    log_queue.put(("log", f"[{level.upper()}] {msg}"))

def stage(name, status):
    state["stages"][name] = status
    log_queue.put(("stage", f"{name}:{status}"))

def is_shorts(script=None):
    script = script if script is not None else (state.get("script") or {})
    return state.get("video_type") == "shorts" or script.get("video_type") == "shorts"
