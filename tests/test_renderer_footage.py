"""Direct renderer entry points enforce the same clip receipt as Studio."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import wave

import pytest

from apis.stock_video import verify_footage
from video.renderers import html as renderer


@pytest.fixture(scope="module")
def clip_bytes(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required for the local playable clip fixture")
    path = tmp_path_factory.mktemp("renderer-footage") / "clip.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=12:duration=2",
        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path),
    ], capture_output=True, check=True, timeout=20)
    return path.read_bytes()


@pytest.fixture
def workspace(tmp_path, monkeypatch, clip_bytes):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(renderer.config, "OUTPUT_DIR", str(root))
    monkeypatch.setattr(renderer, "_copy_vendor", lambda project: None)
    clip = root / "water-filter.mp4"
    clip.write_bytes(clip_bytes)
    audio = root / "narration.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 16000)
    receipt = {
        "version": 1, "provider": "pixabay", "provider_id": "12", "profile_id": "default",
        "script_signature": "a" * 64, "scene_id": "one", "request_sha256": "b" * 64,
        "sha256": hashlib.sha256(clip_bytes).hexdigest(), "bytes": len(clip_bytes),
        "width": 640, "height": 360, "duration": 2, "query": "water filter",
        "subject_terms": ["water filter"], "matched_subjects": ["water filter"],
        "exclude_terms": [], "theme": "survival", "palette": [], "palette_score": None,
        "crop_fraction": 1, "author": "Camera Artist", "license": "Pixabay Content License",
        "license_url": "https://pixabay.com/service/license-summary/",
        "validation": "metadata_and_decoded_frames",
    }
    footage = {"path": str(clip), "source_url": "https://pixabay.com/videos/water-filter-12/",
               "credit": "Video by Camera Artist on Pixabay", "in_point": 0, "selection": receipt}
    plan = {"version": 1, "fps": 24, "width": 640, "height": 360, "duration": 2,
            "title": "Survival technology", "mode": "hybrid", "profile_id": "default",
            "script_signature": "a" * 64,
            "scenes": [{"id": "one", "section_id": "section", "template": "footage",
                        "start": 0, "duration": 2, "title": "Clean drinking water",
                        "items": [], "beats": [], "footage": footage}]}
    return root, audio, clip, plan


@pytest.mark.parametrize("portable", [False, True])
def test_direct_prepare_preserves_verified_receipt_and_owner(workspace, portable):
    root, audio, clip, plan = workspace
    if portable:
        plan["scenes"][0]["footage"]["path"] = clip.name
    original = copy.deepcopy(plan)
    entry = renderer.prepare_motion_project(plan, str(audio), str(root))
    saved = json.loads((entry.parent / "project.json").read_text())
    footage = saved["scenes"][0]["footage"]
    assert saved["profile_id"] == plan["profile_id"]
    assert footage["selection"] == original["scenes"][0]["footage"]["selection"]
    assert verify_footage(footage, entry.parent, script_signature=plan["script_signature"],
                          profile_id=plan["profile_id"], scene_id="one") is True
    assert (entry.parent / footage["path"]).read_bytes() == clip.read_bytes()
    assert plan == original


@pytest.mark.parametrize("mutation", [
    "file_bytes", "wrong_script", "wrong_profile", "wrong_scene", "missing_fingerprint", "malformed_receipt",
])
def test_direct_prepare_rejects_changed_or_foreign_footage_before_copy(workspace, mutation):
    root, audio, clip, plan = workspace
    receipt = plan["scenes"][0]["footage"]["selection"]
    if mutation == "file_bytes":
        data = bytearray(clip.read_bytes())
        data[-1] ^= 1
        clip.write_bytes(data)  # Same byte count is insufficient provenance.
    elif mutation == "wrong_script":
        receipt["script_signature"] = "c" * 64
    elif mutation == "wrong_profile":
        receipt["profile_id"] = "d" * 32
    elif mutation == "wrong_scene":
        receipt["scene_id"] = "other-scene"
    elif mutation == "missing_fingerprint":
        receipt.pop("sha256")
    else:
        plan["scenes"][0]["footage"]["selection"] = None
    with pytest.raises(ValueError, match="Footage|footage"):
        renderer.prepare_motion_project(plan, str(audio), str(root))
    assert not (root / "motion").exists()


def test_scene_edit_staging_can_verify_source_in_trusted_workspace(workspace, tmp_path):
    root, audio, clip, plan = workspace
    staging = tmp_path / "scene-edit-stage"
    entry = renderer.prepare_motion_project(plan, str(audio), str(staging))
    saved = json.loads((entry.parent / "project.json").read_text())
    assert (entry.parent / saved["scenes"][0]["footage"]["path"]).read_bytes() == clip.read_bytes()
    assert not (root / "motion").exists()


def test_valid_receipt_cannot_bless_file_outside_both_trusted_roots(workspace, tmp_path):
    root, audio, clip, plan = workspace
    outside = tmp_path / "unrelated-project.mp4"
    outside.write_bytes(clip.read_bytes())
    plan["scenes"][0]["footage"]["path"] = str(outside)
    staging = tmp_path / "scene-edit-stage"
    with pytest.raises(ValueError, match="Missing or unsupported local media"):
        renderer.prepare_motion_project(plan, str(audio), str(staging))
    assert not (staging / "motion").exists()


def test_direct_render_rejects_tampered_clip_before_footage_probe_or_copy(workspace, monkeypatch):
    root, audio, clip, plan = workspace
    clip.write_bytes(b"changed clip with no matching hash")
    monkeypatch.setattr(renderer, "_dependencies", lambda: ("node", Path("cli.mjs"), "ffprobe"))
    probes = []

    def probe(path, *args):
        probes.append(path)
        assert path == audio
        return {"streams": [{"codec_type": "audio"}], "format": {"duration": "2"}}

    monkeypatch.setattr(renderer, "_probe", probe)
    monkeypatch.setattr(renderer, "_run_render", lambda *args: pytest.fail("Invalid clip reached rendering"))
    with pytest.raises(renderer.MotionRenderError, match="Rebuild Visuals"):
        renderer.render_motion_video({}, str(audio), str(root), plan)
    assert probes == [audio]
    assert not (root / "motion").exists()


def test_legacy_without_receipt_stays_prepareable(workspace):
    root, audio, clip, plan = workspace
    plan["scenes"][0]["footage"].pop("selection")
    plan.pop("profile_id")
    entry = renderer.prepare_motion_project(plan, str(audio), str(root))
    saved = json.loads((entry.parent / "project.json").read_text())
    assert "selection" not in saved["scenes"][0]["footage"]
    assert (entry.parent / saved["scenes"][0]["footage"]["path"]).read_bytes() == clip.read_bytes()
