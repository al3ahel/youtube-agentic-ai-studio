"""Run a narrated reference comparison through the production pipeline.

    python renderers/hyperframes/comparable_demo.py --prepare-only
    python renderers/hyperframes/comparable_demo.py --render --output OUTPUT
    python renderers/hyperframes/comparable_demo.py --render --output OUTPUT --resume
    python renderers/hyperframes/comparable_demo.py --output OUTPUT --export-checkpoint SAVED
    python renderers/hyperframes/comparable_demo.py --output NEW_OUTPUT --resume-from-checkpoint SAVED --render

The original script and pinned reference are in fixtures/armor-*.json. The
reference's intended 29.49 seconds rounds to 885 frames / 29.5 seconds at 30 fps.
The narrator records every section and measures its actual word events. A
bounded number of fresh recordings may adjust the voice rate; no spoken audio
is cut, stretched, replaced with silence, or taken from the reference. At most
0.75 seconds of labelled end silence can pad a complete recording to the exact
frame clock. --resume reuses verified recordings and acquired museum assets.

--export-checkpoint saves a verified, portable directory of the complete raw
narration, measured timing, original voice attempt and acquired assets. Export
does not call any model or voice service. --resume-from-checkpoint restores those
bytes into a new output directory; the ordinary resumed run then makes fresh
planning, sound and review requests. A checkpoint is not a new inference result
or an approval, and the source comparison report is retained with it.

An explicit --replay-visual-responses BINDING option can reapply immutable
captured local proposals after a validator repair. It requires unchanged source
inputs, prompt/schema builders and model identity, runs all normal validators,
and labels the result as recorded-response replay rather than new inference.
Sound and encoded visual review still make real model requests.

The normal visual planner chooses all shots. The normal automatic audio path
chooses and mixes licensed recordings. The normal renderer and encoded review
and repair loop produce evidence. There is no upload step, fixture storyboard,
manual sound cue list, or alternate demonstration renderer.

By default, an unavailable model or failed semantic review produces a blocked
report and a nonzero exit status. --allow-technical-fallback permits an explicitly
incomplete technical demonstration using the application's ordinary fallbacks;
it never makes that result proof of a successful model-backed comparison.

Saved settings and the selected channel's model/voice configuration are used.
This process alone sets the required format, Automatic sound, free-only routing
and enabled rendered review. No persistent settings are changed.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / "fixtures"
MAX_TAIL_SECONDS = .75
MAX_RESPONSE_CAPTURE_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_CAPTURES = 64
VISUAL_BINDING_FILES = ("script.json", "narration.wav", "narration.timings.json", "timeline.json", "image_map.json")
VISUAL_BINDING_FUNCTIONS = {"_normalise_timeline", "_offline_scenes", "_ai_schema", "_request_design", "_request_design_impl"}
VISUAL_BINDING_SUPPORT = ("agents/shot_planner.py", "agents/art_direction.py", "agents/object_direction.py")
CHECKPOINT_REQUIRED = ("script.json", "reference.json", "narration.wav", "narration.timings.json",
                       "narration.srt", "narration_attempts.json", "timeline.json")
CHECKPOINT_OPTIONAL = ("narration.service.json", "input_preparation.json", "museum_assets.json", "image_map.json", "image_binding.json",
                       "credits.json", "scene_plan.json", "visual_response_binding.json", "comparison_report.json",
                       "visual_qa.json", "render_audio.json", "sound_design.json", "audio_master.json", "audio_credits.json")
CHECKPOINT_VOICE_FILES = ("narration.wav", "narration.timings.json", "narration.srt", "narration.service.json")
MAX_CHECKPOINT_FILES = 256
MAX_CHECKPOINT_FILE_BYTES = 64 * 1024 * 1024
MAX_CHECKPOINT_BYTES = 256 * 1024 * 1024
MAX_CHECKPOINT_MANIFEST_BYTES = 1024 * 1024


class ComparisonBlocked(RuntimeError):
    """A required real-pipeline stage did not produce sufficient evidence."""


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _checkpoint_root(value):
    path = Path(value).expanduser().absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ComparisonBlocked("Checkpoint paths cannot contain symbolic links.")
    return path.resolve()


def _checkpoint_name(name):
    """Only the comparison's data files can enter a portable checkpoint."""
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name or "\0" in name
            or PurePosixPath(name).is_absolute() or PurePosixPath(name).as_posix() != name
            or any(part in {".", ".."} for part in name.split("/"))):
        raise ComparisonBlocked("Checkpoint contains an unsafe relative path.")
    allowed = name in (*CHECKPOINT_REQUIRED, *CHECKPOINT_OPTIONAL)
    allowed |= bool(re.fullmatch(r"images/met-\d+\.jpg", name))
    allowed |= bool(re.fullmatch(r"model_responses/response-\d+\.json", name))
    allowed |= bool(re.fullmatch(r"soundtracks/[a-f0-9]{32}\.(?:wav|mp3|m4a|ogg|aac|flac|opus)", name))
    if re.fullmatch(r"voice_attempts/attempt-\d+/[^/]+", name):
        allowed |= PurePosixPath(name).name in CHECKPOINT_VOICE_FILES
    if not allowed:
        raise ComparisonBlocked("Checkpoint contains a file outside the comparison data allowlist.")
    return name


def _checkpoint_file(root, name):
    name = _checkpoint_name(name)
    path = root / name
    cursor = root
    for part in PurePosixPath(name).parts:
        cursor /= part
        if cursor.is_symlink():
            raise ComparisonBlocked("Checkpoint files cannot contain symbolic links.")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ComparisonBlocked(f"Checkpoint is missing a regular data file: {name}")
    return path


def _verify_checkpoint_files(root, records):
    if not isinstance(records, list) or not 0 < len(records) <= MAX_CHECKPOINT_FILES:
        raise ComparisonBlocked("Checkpoint file count exceeds its bound or is empty.")
    names, total = set(), 0
    for row in records:
        if not isinstance(row, dict):
            raise ComparisonBlocked("Checkpoint file record is invalid.")
        name = _checkpoint_name(row.get("path"))
        size, digest = row.get("bytes"), row.get("sha256")
        if (name.casefold() in names or type(size) is not int or not 0 <= size <= MAX_CHECKPOINT_FILE_BYTES
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise ComparisonBlocked("Checkpoint has duplicate files, an invalid hash or an excessive file size.")
        names.add(name.casefold())
        total += size
        if total > MAX_CHECKPOINT_BYTES:
            raise ComparisonBlocked("Checkpoint total size exceeds its bound.")
        path = _checkpoint_file(root, name)
        if path.stat().st_size != size or _sha(path) != digest:
            raise ComparisonBlocked(f"Checkpoint bytes or SHA-256 do not match: {name}")
    if not set(CHECKPOINT_REQUIRED).issubset(names):
        raise ComparisonBlocked("Checkpoint lacks the complete narration and measured inputs required for resume.")
    return names


def _verify_checkpoint_inputs(root, names):
    """Rebuild timing on private copies, never changing the source evidence."""
    import config
    from video.timeline import build_timeline, script_signature
    script, reference = _read(root / "script.json"), _read(root / "reference.json")
    timing, timeline = _read(root / "narration.timings.json"), _read(root / "timeline.json")
    history = _read(root / "narration_attempts.json")
    if (not all(isinstance(value, dict) for value in (script, reference, timing, timeline))
            or not isinstance(script.get("sections"), list)
            or not all(isinstance(row, dict) for row in script["sections"])
            or not isinstance(timing.get("benchmark_tail"), dict)
            or not isinstance(reference.get("target"), dict)):
        raise ComparisonBlocked("Checkpoint script, timing or reference metadata is invalid.")
    if (not isinstance(history, list) or not 0 < len(history) <= 100
            or not all(isinstance(row, dict) for row in history)):
        raise ComparisonBlocked("Checkpoint has no bounded narration attempt history.")
    source_hash = timing.get("benchmark_tail", {}).get("source_audio_sha256")
    selected = next((row for row in history if isinstance(row, dict) and row.get("status") == "recorded"
                     and row.get("audio_sha256") == source_hash and source_hash), None)
    if not selected or not re.fullmatch(r"voice_attempts/attempt-\d+", str(selected.get("directory", ""))):
        raise ComparisonBlocked("Checkpoint lacks the original voice attempt bound to the complete narration.")
    attempt = selected["directory"]
    required_voice = [f"{attempt}/{name}" for name in CHECKPOINT_VOICE_FILES[:3]]
    if not set(required_voice).issubset(names):
        raise ComparisonBlocked("Checkpoint is missing raw voice, timing or subtitles for the selected attempt.")
    if (_sha(_checkpoint_file(root, required_voice[0])) != source_hash
            or selected.get("script_signature") != script_signature(script)):
        raise ComparisonBlocked("Checkpoint voice attempt does not match its recorded script and audio.")
    target = reference.get("target", {})
    settings = {"SHORTS_WIDTH": target.get("width"), "SHORTS_HEIGHT": target.get("height"),
                "SHORTS_FPS": target.get("fps")}
    if (not all(type(value) is int for value in settings.values())
            or not 64 <= settings["SHORTS_WIDTH"] <= 4096 or settings["SHORTS_WIDTH"] % 2
            or not 64 <= settings["SHORTS_HEIGHT"] <= 4096 or settings["SHORTS_HEIGHT"] % 2
            or not 1 <= settings["SHORTS_FPS"] <= 60):
        raise ComparisonBlocked("Checkpoint reference format is invalid.")
    try:
        with tempfile.TemporaryDirectory(prefix="yaas-checkpoint-verify-") as temporary:
            work = Path(temporary)
            audio, padded = _pad_complete_narration(root / attempt, work, float(target["duration_seconds"]))
            if _sha(audio) != _sha(root / "narration.wav") or padded != timing:
                raise ComparisonBlocked("Checkpoint complete narration differs from its original measured voice attempt.")
            if (work / "narration.srt").read_bytes() != (root / "narration.srt").read_bytes():
                raise ComparisonBlocked("Checkpoint subtitles differ from the original voice attempt.")
            with config.profile_context(settings):
                rebuilt = build_timeline(script, audio, str(work), "shorts")
            if rebuilt != timeline:
                raise ComparisonBlocked("Checkpoint timeline differs from the verified recorded narration.")
    except (KeyError, TypeError, ValueError, OSError, EOFError, wave.Error) as exc:
        raise ComparisonBlocked("Checkpoint narration or timing validation failed.") from exc
    if "museum_assets.json" in names:
        museum = _read(root / "museum_assets.json")
        if not isinstance(museum, list) or len(museum) > 50:
            raise ComparisonBlocked("Checkpoint museum metadata is invalid.")
        acquired = {}
        for row in museum:
            if not isinstance(row, dict) or row.get("status") != "acquired":
                continue
            name = _checkpoint_name(row.get("file"))
            if (name not in names or name != f"images/met-{row.get('object_id')}.jpg"
                    or row.get("is_public_domain") is not True or _sha(root / name) != row.get("sha256")):
                raise ComparisonBlocked("Checkpoint museum asset differs from its acquisition evidence.")
            acquired[name] = row["sha256"]
        if "image_map.json" in names:
            image_map = _read(root / "image_map.json")
            if (not isinstance(image_map, dict) or not all(isinstance(rows, list)
                    and all(isinstance(name, str) and name in acquired for name in rows) for rows in image_map.values())):
                raise ComparisonBlocked("Checkpoint image map contains unverified assets.")
    elif "image_map.json" in names:
        raise ComparisonBlocked("Checkpoint image map lacks its acquisition metadata.")
    if "render_audio.json" in names:
        sound = _read(root / "render_audio.json")
        if not isinstance(sound, dict):
            raise ComparisonBlocked("Checkpoint soundtrack metadata is invalid.")
        soundtrack = _checkpoint_name(sound.get("path"))
        if (soundtrack not in names or sound.get("audio_sha256") != timing["audio_sha256"]
                or _sha(root / soundtrack) != sound.get("soundtrack_sha256")):
            raise ComparisonBlocked("Checkpoint soundtrack differs from its audio binding.")
    return {"script_signature": script_signature(script), "audio_sha256": timing["audio_sha256"],
            "timeline_signature": timeline["timeline_signature"], "selected_voice_attempt": attempt}


def _export_checkpoint(source, destination):
    """Export allowlisted recorded inputs; no provider calls or settings writes."""
    source, destination = _checkpoint_root(source), _checkpoint_root(destination)
    if not source.is_dir() or destination.exists() or destination.is_relative_to(source):
        raise ComparisonBlocked("Export needs an existing run and a new checkpoint directory outside it.")
    paths = set(CHECKPOINT_REQUIRED)
    paths.update(name for name in CHECKPOINT_OPTIONAL if (source / name).exists() or (source / name).is_symlink())
    for pattern in ("voice_attempts/attempt-*/*", "images/met-*.jpg", "model_responses/response-*.json", "soundtracks/*"):
        for path in source.glob(pattern):
            name = path.relative_to(source).as_posix()
            if pattern.startswith("voice_attempts") and path.name not in CHECKPOINT_VOICE_FILES:
                continue
            paths.add(_checkpoint_name(name))
    if len(paths) > MAX_CHECKPOINT_FILES:
        raise ComparisonBlocked("Checkpoint file count exceeds its bound.")
    records, total = [], 0
    for name in sorted(paths):
        path = _checkpoint_file(source, name)
        size = path.stat().st_size
        total += size
        if size > MAX_CHECKPOINT_FILE_BYTES or total > MAX_CHECKPOINT_BYTES:
            raise ComparisonBlocked("Checkpoint data exceeds its size bound.")
        records.append({"path": name, "bytes": size, "sha256": _sha(path)})
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".yaas-checkpoint-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "checkpoint"
        payload = staged / "files"
        payload.mkdir(parents=True)
        for row in records:
            target = payload / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_checkpoint_file(source, row["path"]), target)
        names = _verify_checkpoint_files(payload, records)
        inputs = _verify_checkpoint_inputs(payload, names)
        manifest = {"version": 1, "kind": "yaas_comparison_checkpoint", "created_at": datetime.now(timezone.utc).isoformat(),
                    "files": records, "inputs": inputs, "new_inference": False, "approval_granted": False,
                    "scope": "Recorded inputs and source receipts only. Resume reruns normal direction, sound and review; export is not a new comparison."}
        _write(staged / "checkpoint.json", manifest)
        staged.rename(destination)
    return manifest


def _restore_checkpoint(source, destination):
    """Verify all bytes before publishing a new runnable output directory."""
    source, destination = _checkpoint_root(source), _checkpoint_root(destination)
    if destination.exists() or destination.is_relative_to(source):
        raise ComparisonBlocked("Restore requires a new output directory outside the checkpoint.")
    manifest_path = source / "checkpoint.json"
    if (manifest_path.is_symlink() or not manifest_path.is_file()
            or manifest_path.stat().st_size > MAX_CHECKPOINT_MANIFEST_BYTES):
        raise ComparisonBlocked("Checkpoint manifest is unavailable or exceeds its size bound.")
    manifest = _read(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or manifest.get("kind") != "yaas_comparison_checkpoint":
        raise ComparisonBlocked("Unsupported checkpoint manifest.")
    payload = _checkpoint_root(source / "files")
    names = _verify_checkpoint_files(payload, manifest.get("files"))
    inputs = _verify_checkpoint_inputs(payload, names)
    if inputs != manifest.get("inputs"):
        raise ComparisonBlocked("Checkpoint input provenance does not match its verified contents.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".yaas-restore-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "output"
        staged.mkdir()
        for row in manifest["files"]:
            target = staged / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_checkpoint_file(payload, row["path"]), target)
        _verify_checkpoint_files(staged, manifest["files"])
        receipt = {"version": 1, "mode": "verified_checkpoint_restore", "restored_at": datetime.now(timezone.utc).isoformat(),
                   "checkpoint_manifest_sha256": _sha(manifest_path), "inputs": inputs, "new_inference_during_restore": False,
                   "subsequent_run": "Normal resume: recorded narration/assets may be reused; direction, sound and review make fresh requests unless visual replay is explicitly selected."}
        archive = staged / "checkpoint_source"
        archive.mkdir()
        shutil.copyfile(manifest_path, archive / "checkpoint.json")
        if "comparison_report.json" in names:
            shutil.copyfile(staged / "comparison_report.json", archive / "comparison_report.json")
        _write(staged / "checkpoint_origin.json", receipt)
        staged.rename(destination)
    return receipt


def _error(exc):
    # Provider errors sometimes contain a request URL. Reuse the production
    # redactor rather than allowing credentials into a distributable report.
    from apis.openrouter import _redact
    return {"type": type(exc).__name__, "message": _redact(str(exc))[:1200]}


def _package(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return None


@contextmanager
def _capture_local_responses(output, stage):
    """Observe actual local replies without modifying requests or returned data."""
    from apis import local_llm
    from apis.openrouter import _redact
    folder = output / "model_responses"
    folder.mkdir(exist_ok=True)
    previous = [int(match.group(1)) for path in folder.glob("response-*.json")
                if (match := re.fullmatch(r"response-(\d+)\.json", path.name))]
    sequence = max(previous, default=0)
    original = local_llm._request_json
    audit = {"directory": "model_responses", "files": [], "errors": [], "omitted_calls": 0,
             "max_bytes_per_file": MAX_RESPONSE_CAPTURE_BYTES, "max_calls_this_run": MAX_RESPONSE_CAPTURES,
             "response_mutation": False, "request_text_and_images_saved": False}

    def save(record, response=None):
        nonlocal sequence
        if len(audit["files"]) >= MAX_RESPONSE_CAPTURES:
            audit["omitted_calls"] += 1
            return
        sequence += 1
        try:
            if response is not None:
                text = _redact(json.dumps(response, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
                record.update(response_text=text, redacted=True, truncated=False,
                              redacted_response_json_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
            encoded = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            while len(encoded) > MAX_RESPONSE_CAPTURE_BYTES:
                record["response_text"] = record["response_text"][:int(len(record["response_text"]) * .8)]
                record["truncated"] = True
                encoded = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            path = folder / f"response-{sequence:04d}.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(encoded)
            temporary.replace(path)
            audit["files"].append(path.relative_to(output).as_posix())
        except (OSError, ValueError, TypeError) as exc:
            audit["errors"].append(_error(exc))

    def observed(base_url, endpoint, **kwargs):
        if endpoint != "/api/chat":
            return original(base_url, endpoint, **kwargs)
        body = kwargs.get("body") if isinstance(kwargs.get("body"), dict) else {}
        record = {"version": 1, "endpoint": endpoint, "stage": stage(), "model": body.get("model"),
                  "started_at": datetime.now(timezone.utc).isoformat(),
                  "native_json_schema": isinstance(body.get("format"), dict),
                  "image_count": sum(len(message.get("images", [])) for message in body.get("messages", [])
                                     if isinstance(message, dict) and isinstance(message.get("images", []), list))}
        try:
            response = original(base_url, endpoint, **kwargs)
        except Exception as exc:
            record.update(status="transport_failed", error=_error(exc), finished_at=datetime.now(timezone.utc).isoformat())
            save(record)
            raise
        record.update(status="received", finished_at=datetime.now(timezone.utc).isoformat())
        save(record, response)
        return response

    local_llm._request_json = observed
    try:
        yield audit
    finally:
        local_llm._request_json = original


def _configuration():
    import config
    from apis.llm import _real_key
    return {
        "route": config.LLM_ROUTE,
        "local_model": config.OLLAMA_MODEL,
        "visual_planner_model": config.VISUAL_PLANNER_MODEL,
        "visual_review_model": config.VISUAL_REVIEW_MODEL,
        "local_review_model": config.OLLAMA_MODEL or None,
        "openrouter_model": config.OPENROUTER_MODEL,
        "gemini_model": config.GEMINI_MODEL,
        "groq_model": config.GROQ_MODEL,
        "hosted_credentials_present": {name: _real_key(getattr(config, name + "_API_KEY", ""))
                                       for name in ("OPENROUTER", "GEMINI", "GROQ")},
        "openrouter_free_only": config.OPENROUTER_FREE_ONLY,
        "narration_engine": "edge-tts",
        "narration_engine_version": _package("edge-tts"),
        "voice": config.VOICE_ID, "initial_voice_rate": config.VOICE_RATE,
        "voice_pitch": config.VOICE_PITCH,
        "sound_mode": config.SOUND_MODE,
        "meaning": "Configured choices only; actual responding providers appear in each stage's evidence.",
    }


def _visual_source_fingerprints():
    source = (ROOT / "agents/visual_director.py").read_text(encoding="utf-8")
    nodes = ast.parse(source).body
    functions = {node.name: hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest()
                 for node in nodes if isinstance(node, ast.FunctionDef) and node.name in VISUAL_BINDING_FUNCTIONS}
    if set(functions) != VISUAL_BINDING_FUNCTIONS:
        raise ComparisonBlocked("The visual prompt/schema source fingerprint could not be established.")
    return functions, {name: _sha(ROOT / name) for name in VISUAL_BINDING_SUPPORT}


def _visual_response_binding(output, report, mode):
    """Bind future replay to measured inputs and the actual prompt/schema code."""
    import config
    functions, support = _visual_source_fingerprints()
    timeline = _read(output / "timeline.json")
    return {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "scope": "Local responses may be reapplied only to these unchanged inputs; reapplication is not new inference.",
            "original_run_started_at": report["started_at"], "script_signature": report["script_sha256"],
            "timeline_signature": timeline["timeline_signature"],
            "files": {name: _sha(output / name) for name in VISUAL_BINDING_FILES},
            "visual_director_functions_sha256": functions, "support_files_sha256": support,
            "local_runtime": report["stages"]["local_runtime"], "configuration": report["configuration"],
            "prompt_settings": {"channel_preset": config.CHANNEL_PRESET, "mode": mode, "user_instructions": ""}}


def _seal_visual_response_binding(binding, output, capture, plan):
    binding = deepcopy(binding)
    binding.update(expected_scene_ids=[scene["id"] for scene in plan["scenes"]],
                   shots_enabled=all(bool(scene.get("shot")) for scene in plan["scenes"]),
                   sealed_at=datetime.now(timezone.utc).isoformat())
    rows = []
    for name in capture["files"]:
        path = output / name
        record = _read(path)
        if record.get("stage") == "visual_planning":
            rows.append({"file": name, "sha256": _sha(path)})
    binding.update(response_captures=rows, expected_batches=len(rows))
    _write(output / "visual_response_binding.json", binding)


@contextmanager
def _replay_visual_responses(binding_path, output, report, mode):
    """Replay only captured generation, keeping normal schema/grounding checks.

    Replacing this narrow call boundary never edits a response, supplies a scene
    directly, or disables a validator. The surrounding planner still builds its
    original prompts, checks every returned row, applies source guards, attaches
    trusted media and records actual surviving decisions.
    """
    if binding_path is None:
        yield None
        return
    import config
    from agents import gemini_client, visual_director
    binding_path = Path(binding_path).resolve()
    binding = _read(binding_path)
    current = _visual_response_binding(output, report, mode)

    def require(condition, message):
        if not condition:
            raise ComparisonBlocked("Visual response replay refused: " + message)

    require(binding.get("version") == 1, "unsupported binding version.")
    for key in ("script_signature", "timeline_signature", "files", "visual_director_functions_sha256",
                "support_files_sha256", "prompt_settings"):
        require(binding.get(key) == current[key], f"the bound {key} changed.")
    previous_model = binding.get("local_runtime", {}).get("model", {})
    current_model = current["local_runtime"].get("model", {})
    require(config.LLM_ROUTE == "local" and previous_model.get("id") == config.OLLAMA_MODEL,
            "the selected local model changed.")
    require(binding.get("local_runtime", {}).get("status") == current["local_runtime"].get("status") == "installed"
            and previous_model.get("digest") and all(previous_model.get(key) == current_model.get(key)
                                                     for key in ("id", "digest", "size")),
            "the installed model identity changed or cannot be verified.")
    captures, expected_ids = binding.get("response_captures"), binding.get("expected_scene_ids")
    require(isinstance(captures, list) and 0 < len(captures) <= 40
            and len(captures) == binding.get("expected_batches"), "the capture list is incomplete.")
    require(isinstance(expected_ids, list) and 0 < len(expected_ids) <= 120
            and len(set(expected_ids)) == len(expected_ids), "scene identities are incomplete.")
    require(isinstance(binding.get("shots_enabled"), bool), "the shot schema version is unspecified.")
    records, returned_ids = [], []
    for capture in captures:
        require(isinstance(capture, dict) and isinstance(capture.get("file"), str), "a capture entry is invalid.")
        relative = Path(capture["file"])
        path = (binding_path.parent / relative).resolve()
        require(not relative.is_absolute() and path.is_relative_to(binding_path.parent)
                and path.is_file() and path.stat().st_size <= MAX_RESPONSE_CAPTURE_BYTES,
                "a bounded capture file is unavailable.")
        require(_sha(path) == capture.get("sha256"), "a capture file hash changed.")
        record = _read(path)
        text = record.get("response_text", "")
        require(record.get("status") == "received" and record.get("stage") == "visual_planning"
                and record.get("native_json_schema") is True and record.get("truncated") is False
                and record.get("model") == previous_model["id"] and record.get("image_count") == 0
                and hashlib.sha256(text.encode()).hexdigest() == record.get("redacted_response_json_sha256"),
                "a capture is incomplete, changed or belongs to another stage/model.")
        response = json.loads(text)
        content = response.get("message", {}).get("content")
        require(response.get("done") is True and response.get("done_reason") == "stop"
                and response.get("model") == previous_model["id"] and isinstance(content, str)
                and content.strip() and "[credential removed]" not in content,
                "a captured generation was unfinished or its content was redacted.")
        design = json.loads(content)
        rows = design.get("scenes") if isinstance(design, dict) else None
        require(isinstance(rows, list) and 0 < len(rows) <= 3
                and all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in rows),
                "captured scene identities are invalid.")
        ids = [row["id"] for row in rows]
        returned_ids.extend(ids)
        records.append({"content": content, "ids": ids, "capture": capture,
                        "started_at": record["started_at"], "finished_at": record["finished_at"]})
    require(returned_ids == expected_ids, "captured batches do not cover the exact ordered scene list.")
    audit = {"mode": "recorded_local_response_replay", "new_visual_inference": False,
             "binding_file": binding_path.name, "binding_sha256": _sha(binding_path),
             "model": previous_model["id"], "model_digest": previous_model["digest"],
             "original_run_started_at": binding["original_run_started_at"], "consumed_batches": [],
             "ordinary_sound_and_vision_generation": True,
             "qualification": "Previously captured real local proposals were reapplied through unchanged prompt/schema builders and the current production validators. This is not new visual inference."}
    report["visual_response_origin"] = audit
    original, consumed = gemini_client.generate, 0

    def generate(prompt, *args, **kwargs):
        nonlocal consumed
        require(consumed < len(records), "the planner requested an unexpected extra batch.")
        row = records[consumed]
        schema = visual_director._ai_schema(row["ids"], mode, shots_enabled=binding["shots_enabled"])
        require(kwargs.get("json_mode") is True and kwargs.get("json_schema") == schema,
                "the requested schema or batch identities changed.")
        info = {"provider": "ollama", "model": previous_model["id"], "finish_reason": "stop",
                "response_origin": "recorded_local_response", "new_inference": False,
                "capture_sha256": row["capture"]["sha256"], "captured_at": row["finished_at"]}
        if kwargs.get("provider_info") is not None:
            kwargs["provider_info"].clear()
            kwargs["provider_info"].update(info)
        audit["consumed_batches"].append({**info, "scene_ids": row["ids"],
                                           "original_started_at": row["started_at"]})
        consumed += 1
        print(f"   → Reapplying recorded local proposal {consumed}/{len(records)} (no new inference)…", flush=True)
        return row["content"]

    gemini_client.generate = generate
    try:
        yield audit
        require(consumed == len(records), "not all captured batches passed through the normal planner.")
    finally:
        gemini_client.generate = original


def _local_runtime_evidence():
    """Record the installed model identity; stage results separately prove use."""
    import config
    from apis import local_llm
    if config.LLM_ROUTE != "local" and not config.OLLAMA_MODEL:
        return {"status": "not_selected"}
    try:
        models = local_llm.list_models()
        selected = config.OLLAMA_MODEL
        model = next((row for row in models if row["id"] in {selected, selected + ":latest"}),
                     models[0] if not selected and models else None)
        if model is None:
            return {"status": "unavailable", "reason": "selected_model_not_installed"}
        details = local_llm._request_json(None, "/api/show", body={"model": model["id"]})
        if details.get("remote_host") or details.get("remote_model"):
            return {"status": "unavailable", "reason": "remote_alias_rejected"}
        runtime = local_llm._request_json(None, "/api/version")
        return {"status": "installed", "runtime": "ollama", "runtime_version": runtime.get("version"),
                "model": model, "capabilities": details.get("capabilities", []),
                "meaning": "Installed identity reported by the loopback service. Actual responding providers are recorded per stage."}
    except (RuntimeError, ValueError, OSError) as exc:
        return {"status": "unavailable", "error": _error(exc)}


def _fetch_museum_assets(reference, output, *, resume=False):
    """Resolve public-domain objects through the museum's actual metadata API."""
    import requests
    from PIL import Image
    folder = output / "images"
    folder.mkdir(exist_ok=True)
    records, image_map, credits = [], {}, []
    previous = {}
    if resume and (output / "museum_assets.json").is_file():
        previous = {row["object_id"]: row for row in _read(output / "museum_assets.json")
                    if isinstance(row, dict) and row.get("status") == "acquired"}
    for item in reference.get("museum_assets", []):
        oid = item["object_id"]
        target = folder / f"met-{oid}.jpg"
        record = {"object_id": oid, "object_url": item["object_url"], "use": item["use"], "status": "unavailable"}
        try:
            cached = previous.get(oid)
            if (cached and target.is_file() and cached.get("sha256") == _sha(target)
                    and cached.get("is_public_domain") is True):
                record = {**cached, "reused": True}
            else:
                print(f"Acquiring public-domain museum object {oid}…", flush=True)
                response = requests.get(item["api_url"], timeout=20)
                response.raise_for_status()
                data = response.json()
                if data.get("objectID") != oid or data.get("isPublicDomain") is not True:
                    raise ValueError("The museum did not verify this exact object as public domain.")
                image_url = data.get("primaryImageSmall") or data.get("primaryImage") or ""
                parsed = urlparse(image_url)
                if (parsed.scheme != "https" or parsed.hostname not in {"images.metmuseum.org", "collectionapi.metmuseum.org"}
                        or parsed.username or parsed.password):
                    raise ValueError("The object has no supported museum-hosted image.")
                temporary = target.with_suffix(".download")
                try:
                    with requests.get(image_url, stream=True, timeout=30) as response:
                        response.raise_for_status()
                        size = 0
                        with temporary.open("wb") as downloaded:
                            for block in response.iter_content(65536):
                                size += len(block)
                                if size > 20 * 1024 * 1024:
                                    raise ValueError("The museum image exceeds the demonstration's bounded download size.")
                                downloaded.write(block)
                    with Image.open(temporary) as decoded:
                        decoded.verify()
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
                record.update(status="acquired", title=data.get("title", item["title"]),
                              date=data.get("objectDate", ""), creator=data.get("artistDisplayName", ""),
                              credit_line=data.get("creditLine", ""), image_url=image_url,
                              is_public_domain=True, license="Public domain / The Met Open Access",
                              acquired_at=datetime.now(timezone.utc).isoformat(), sha256=_sha(target),
                              file=target.relative_to(output).as_posix(), reused=False)
            for section in item["section_ids"]:
                image_map.setdefault(str(section), []).append(str(target))
            credits.append({"file": target.name, "source": "The Metropolitan Museum of Art",
                            "author": record.get("creator", ""), "link": item["object_url"],
                            "credit": f"{record.get('title', item['title'])} · The Met · Public domain",
                            "license": record["license"]})
        except (requests.RequestException, ValueError, OSError) as exc:
            record["error"] = _error(exc)
            print(f"Museum object {oid} unavailable; its image was not substituted.", flush=True)
        records.append(record)
    _write(output / "museum_assets.json", records)
    _write(output / "credits.json", credits)
    _write(output / "image_map.json", {key: [Path(value).relative_to(output).as_posix() for value in values]
                                       for key, values in image_map.items()})
    return image_map, records


def _pad_complete_narration(source, output, target_seconds):
    """Append a bounded silent tail; preserve every original speech sample/time."""
    from video.timeline import audio_signature
    original = source / "narration.wav"
    manifest = _read(source / "narration.timings.json")
    destination = output / "narration.wav"
    with wave.open(str(original), "rb") as audio:
        rate, frames = audio.getframerate(), audio.getnframes()
        count = round(target_seconds * rate)
        tail = (count - frames) / rate
        if not 0 <= tail <= MAX_TAIL_SECONDS:
            raise ComparisonBlocked("The full narration is outside the allowed duration window. No speech was cut or retimed.")
        with wave.open(str(destination), "wb") as target:
            target.setparams(audio.getparams())
            while block := audio.readframes(65536):
                target.writeframesraw(block)
            target.writeframesraw(bytes((count - frames) * audio.getnchannels() * audio.getsampwidth()))
    manifest["benchmark_tail"] = {"source_audio_sha256": audio_signature(original),
                                  "source_duration_seconds": frames / rate,
                                  "appended_silence_seconds": tail,
                                  "speech_samples_changed": False}
    manifest["duration"] = count / rate
    manifest["audio_sha256"] = audio_signature(destination)
    manifest["sections"][-1]["duration"] += tail
    _write(output / "narration.timings.json", manifest)
    shutil.copyfile(source / "narration.srt", output / "narration.srt")
    return str(destination), manifest


def _record_narration(script, output, settings, target, *, resume=False, attempts=4, use_cache=False):
    import config
    from video.narrator import generate_narration
    from video.timeline import build_timeline, script_signature
    identity = script_signature(script)
    history_path = output / "narration_attempts.json"
    history = _read(history_path) if resume and history_path.is_file() else []
    if not isinstance(history, list):
        history = []
    reusable = [row for row in history if row.get("script_signature") == identity
                and row.get("voice") == config.VOICE_ID and row.get("pitch") == config.VOICE_PITCH
                and row.get("engine_version") == _package("edge-tts") and row.get("status") == "recorded"]
    chosen = None
    for row in reusable:
        directory = output / row["directory"]
        if (not directory.resolve().is_relative_to(output.resolve())
                or not (directory / "narration.wav").is_file()
                or _sha(directory / "narration.wav") != row["audio_sha256"]):
            continue
        # Validate the saved audio/script binding before trusting cached timing.
        try:
            build_timeline(script, str(directory / "narration.wav"), str(directory), "shorts")
        except (ValueError, OSError):
            continue
        if target - MAX_TAIL_SECONDS <= row["duration_seconds"] <= target:
            chosen = directory
            break
    reused_recording = chosen is not None
    attempts_before = len(history)
    rate_match = re.fullmatch(r"([+-]\d+)%", str(config.VOICE_RATE))
    if not rate_match:
        raise ValueError("The configured voice rate must be a signed percentage.")
    rate = int(rate_match[1])
    for index in range(attempts if chosen is None else 0):
        directory = output / "voice_attempts" / f"attempt-{len(history) + 1}"
        directory.mkdir(parents=True, exist_ok=False)
        record = {"directory": directory.relative_to(output).as_posix(), "voice": config.VOICE_ID,
                  "pitch": config.VOICE_PITCH, "rate": f"{rate:+d}%", "script_signature": identity,
                  "engine": "edge-tts", "engine_version": _package("edge-tts"), "status": "recording"}
        history.append(record)
        _write(history_path, history)
        print(f"Recording complete narration at {record['rate']} (attempt {index + 1}/{attempts})…", flush=True)
        try:
            with config.profile_context({**settings, "VOICE_RATE": record["rate"]}):
                audio = generate_narration(script, str(directory), use_cache=use_cache)
            timing = _read(directory / "narration.timings.json")
            duration = float(timing["duration"])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("Narration returned no measurable complete audio.")
            record.update(status="recorded", duration_seconds=duration, audio_sha256=_sha(audio),
                          word_events=len(timing.get("words", [])), voice_cache=timing.get("voice_cache"),
                          voice_service=timing.get("voice_service"))
            _write(history_path, history)
            if target - MAX_TAIL_SECONDS <= duration <= target:
                chosen = directory
                break
            # Aim below the frame clock so a tiny measured hold can finish it.
            new_rate = round((1 + rate / 100) * duration / (target - .35) * 100 - 100)
            new_rate = max(-50, min(50, new_rate))
            if new_rate == rate:
                new_rate += 1 if duration > target else -1
            if not -50 <= new_rate <= 50:
                break
            rate = new_rate
        except Exception as exc:
            record.update(status="blocked", error=_error(exc))
            if (directory / "narration.service.json").is_file():
                record["voice_service"] = _read(directory / "narration.service.json")
            _write(history_path, history)
            raise
    if chosen is None:
        raise ComparisonBlocked("The complete recordings did not fit the reference duration after bounded voice-rate attempts. They are retained for inspection.")
    audio, timing = _pad_complete_narration(chosen, output, target)
    return audio, {"status": "recorded", "engine": "edge-tts", "voice": config.VOICE_ID,
                   "recording_reused": reused_recording, "new_recording_attempts": len(history) - attempts_before,
                   "selected_attempt": chosen.relative_to(output).as_posix(), "attempts": history,
                   "word_events": len(timing.get("words", [])), "sections": timing["sections"],
                   "voice_service": timing.get("voice_service"),
                   "duration_seconds": timing["duration"], "audio_sha256": timing["audio_sha256"],
                   "tail": timing["benchmark_tail"]}


def _verify_encoded(path, target):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ComparisonBlocked("FFprobe is required to verify the encoded comparison.")
    result = subprocess.run([ffprobe, "-v", "error", "-count_frames", "-show_streams", "-show_format",
                             "-of", "json", str(path)], capture_output=True, text=True, check=True, timeout=90)
    probe = json.loads(result.stdout)
    video = next((row for row in probe["streams"] if row.get("codec_type") == "video"), {})
    audio = next((row for row in probe["streams"] if row.get("codec_type") == "audio"), {})
    checks = {
        "dimensions": [video.get("width"), video.get("height")] == [target["width"], target["height"]],
        "frame_rate": Fraction(video.get("avg_frame_rate", "0")) == target["fps"],
        "decoded_frame_count": int(video.get("nb_read_frames", 0)) == target["frames"],
        "video_codec": video.get("codec_name") == target["video_codec"],
        "audio_codec": audio.get("codec_name") == target["audio_codec"],
        "duration": abs(float(probe["format"].get("duration", 0)) - target["duration_seconds"]) < 2 / target["fps"],
    }
    return {"status": "verified" if all(checks.values()) else "failed", "checks": checks,
            "sha256": _sha(path), "bytes": Path(path).stat().st_size, "probe": probe}


def run(args):
    import config
    from agents.visual_director import has_model_visual_contribution, plan_scenes
    from studio.channels import get_profile
    from studio.profile_context import capture_profile_settings
    from studio import runner
    from studio.state import job_lock, state, stop_event
    from video.timeline import build_timeline, script_signature
    from video.renderers.html import prepare_motion_project, render_motion_video

    script, reference = _read(FIXTURES / "armor-script.json"), _read(FIXTURES / "armor-reference.json")
    target = reference["target"]
    output = args.output.expanduser().resolve()
    restored = None
    if getattr(args, "resume_from_checkpoint", None) is not None:
        restored = _restore_checkpoint(args.resume_from_checkpoint, args.output)
        args.resume = True
        print("Verified checkpoint restored. Reusing matching recorded narration/assets; normal direction, sound and review will make fresh requests.", flush=True)
    replay_binding = getattr(args, "replay_visual_responses", None)
    if replay_binding is not None and not args.resume:
        raise ComparisonBlocked("Recorded visual response replay requires --resume and its unchanged measured inputs.")
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise ComparisonBlocked("Choose a new output folder or use --resume for this comparison's verified recordings.")
    output.mkdir(parents=True, exist_ok=True)
    if args.resume and (output / "script.json").is_file() and script_signature(_read(output / "script.json")) != script_signature(script):
        raise ComparisonBlocked("This output folder contains a different script. Use a new comparison folder.")
    profile = get_profile(args.profile)
    settings = capture_profile_settings(profile["id"])
    settings.update(SHORTS_WIDTH=target["width"], SHORTS_HEIGHT=target["height"], SHORTS_FPS=target["fps"],
                    OUTPUT_DIR=str(output), IMAGES_DIR=str(output / "images"), SOUND_MODE="auto",
                    OPENROUTER_FREE_ONLY=True, VISUAL_AI_ENABLED=True, VISUAL_REVIEW_ENABLED=True)
    if args.local_model:
        if not config._valid_setting("OLLAMA_MODEL", args.local_model):
            raise ValueError("Choose a valid installed local model name.")
        local_url = args.local_url or settings.get("OLLAMA_BASE_URL", config.OLLAMA_BASE_URL)
        if not config._valid_setting("OLLAMA_BASE_URL", local_url):
            raise ValueError("The local model address must be a supported loopback URL.")
        settings.update(LLM_ROUTE="local", OLLAMA_MODEL=args.local_model, OLLAMA_BASE_URL=local_url)
    elif args.local_url:
        raise ValueError("Use --local-model when choosing a one-run local model URL.")
    report = {"version": 1, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
              "reference": reference, "profile_id": profile["id"], "script_sha256": script_signature(script),
              "allow_technical_fallback": args.allow_technical_fallback, "stages": {}, "requirements_unmet": [],
              "comparison_complete": False, "reference_quality_parity_claimed": False,
              "subjective_audio_listening": "not_performed_by_this_harness",
              "implementation": "production narrator, timeline, visual planner, studio sound path, renderer and encoded review loop"}
    if restored is not None:
        report["checkpoint_origin"] = restored
    destination = output / "comparison_report.json"
    _write(output / "script.json", script)
    _write(output / "reference.json", reference)
    _write(destination, report)

    def checkpoint(name, value):
        report["stages"][name] = value
        _write(destination, report)

    def require(condition, message):
        if condition:
            return
        report["requirements_unmet"].append(message)
        _write(destination, report)
        if not args.allow_technical_fallback:
            raise ComparisonBlocked(message)

    if not job_lock.acquire(blocking=False):
        raise ComparisonBlocked("A studio job is already running in this process.")
    previous_state, was_stopped = deepcopy(state), stop_event.is_set()
    try:
        stop_event.clear()
        state.update(script=script, profile_id=profile["id"], output_dir=str(output))
        active_stage = "configuration"
        with config.profile_context(settings), _capture_local_responses(output, lambda: active_stage) as capture:
            report["local_response_capture"] = capture
            report["configuration"] = _configuration()
            report["configuration"]["run_only_overrides"] = ["1080x1920@30fps", "Automatic sound", "free-only OpenRouter", "rendered semantic review enabled"]
            _write(destination, report)
            checkpoint("local_runtime", _local_runtime_evidence())
            active_stage = "narration"
            voice, narration = _record_narration(script, output, settings, target["duration_seconds"],
                                                 resume=args.resume, attempts=args.voice_attempts,
                                                 use_cache=args.use_cached_voice)
            checkpoint("narration", narration)
            state["audio_path"] = voice
            timeline = build_timeline(script, voice, str(output), "shorts")
            checkpoint("timeline", {key: timeline[key] for key in ("duration", "audio_duration", "total_frames", "fps", "width", "height", "timing_source", "timeline_signature")})
            state["timeline"] = timeline
            active_stage = "museum_assets"
            image_map, museum = _fetch_museum_assets(reference, output, resume=args.resume)
            checkpoint("museum_assets", {"assets": museum, "acquired": sum(row["status"] == "acquired" for row in museum)})
            state["image_map"] = image_map
            runner._save_image_map(output, image_map)
            active_stage = "visual_planning"
            visual_mode = "hybrid" if image_map else "motion"
            binding = None
            if replay_binding is None:
                binding = _visual_response_binding(output, report, visual_mode)
                _write(output / "visual_response_binding.json", binding)
                report["visual_response_origin"] = {"mode": "live_generation_path", "recorded_response_replay": False,
                                                     "binding_file": "visual_response_binding.json"}
            with _replay_visual_responses(replay_binding, output, report, visual_mode):
                plan = plan_scenes(script, timeline, mode=visual_mode, output_dir=str(output), use_ai=True,
                                   image_map=image_map, use_project_photos=True,
                                   stop_event=stop_event, profile_id=profile["id"])
            if binding is not None:
                _seal_visual_response_binding(binding, output, capture, plan)
            plan["timeline_signature"] = timeline["timeline_signature"]
            plan["audio_duration"] = timeline["audio_duration"]
            _write(output / "scene_plan.json", plan)
            state["visual_plan"] = plan
            checkpoint("visual_planning", {"ai_status": plan.get("ai_status"), "shot_count": len(plan["scenes"]),
                                           "section_count": len(timeline["sections"]),
                                           "shot_report": plan.get("shot_report"), "direction_report": plan.get("direction_report"),
                                           "techniques": dict(Counter(row.get("shot", {}).get("technique", "legacy") for row in plan["scenes"])),
                                           "warnings": plan.get("warnings", [])})
            require(plan.get("ai_status", {}).get("status") == "planned",
                    "The visual planner did not return an accepted model response; local template output is not a model-backed demonstration.")
            require(has_model_visual_contribution(plan),
                    "No accepted model decisions changed the rendered visual design; a generated response alone is not model-backed direction.")
            require(all(row.get("shot") for row in plan["scenes"]), "One or more rendered scenes lack shot-level direction.")
            require(len(plan["scenes"]) > len(timeline["sections"]), "The storyboard did not demonstrate multiple shots within narration sections.")
            active_stage = "automatic_sound"
            soundtrack = runner._mix_project_audio(voice, timeline, output, profile["id"])
            sound = _read(output / "render_audio.json")
            design = sound.get("sound_design", {})
            applied = {"music": int(bool(design.get("music"))), "ambience": int(bool(design.get("ambience"))),
                       "events": len(design.get("events", []))}
            checkpoint("automatic_sound", {"planner": design.get("planner"), "applied_layers": applied,
                                           "sound_design": design, "mastering": sound.get("audio_master"),
                                           "soundtrack_sha256": sound.get("soundtrack_sha256"), "credits": sound.get("credits", [])})
            require(design.get("planner", {}).get("status") == "ai",
                    "Automatic sound used conservative local rules rather than an accepted free/local model response.")
            require(sum(applied.values()) > 0,
                    "Automatic sound selected no usable added recordings; narration alone does not demonstrate the requested sound design.")
            active_stage = "project_preparation"
            entry = prepare_motion_project(plan, soundtrack, str(output))
            checkpoint("project_preparation", {"status": "prepared", "entry": str(entry.relative_to(output))})
            if args.prepare_only:
                report["status"] = "prepared_incomplete" if report["requirements_unmet"] else "prepared"
                report["requirements_unmet"].append("Encoding and rendered review have not run (--prepare-only).")
                _write(destination, report)
                return 0 if not args.allow_technical_fallback else 2

            active_stage = "encoding"
            def render(candidate):
                return render_motion_video(script, soundtrack, str(output), candidate, video_type="shorts",
                                           stop_event=stop_event, progress=lambda text: print(text, flush=True))
            video = render(plan)
            checkpoint("encoding", {"status": "encoded", "file": str(Path(video).relative_to(output))})
            active_stage = "render_review"
            from agents.render_review import review_and_repair
            try:
                video, plan, review = review_and_repair(video, plan, str(output), render=render, script=script,
                                                       max_repairs=args.max_repairs, stop_event=stop_event,
                                                       progress=lambda text: print(text, flush=True))
            except Exception:
                prepare_motion_project(plan, soundtrack, str(output))
                raise
            if review.get("repair_history"):
                prepare_motion_project(plan, soundtrack, str(output))
            _write(output / "scene_plan.json", plan)
            _write(output / "visual_qa.json", review)
            checkpoint("render_review", review)
            active_stage = "encoded_verification"
            verification = _verify_encoded(video, target)
            checkpoint("encoded_verification", verification)
            report["video"] = str(Path(video).relative_to(output))
            require(verification["status"] == "verified", "The encoded comparison does not match the complete target media specification.")
            require(has_model_visual_contribution(plan),
                    "The accepted film retains no verified model contribution to its rendered visual design.")
            require(review.get("vision", {}).get("complete") is True,
                    "Rendered semantic review is incomplete or unreliable; technical checks are not a substitute.")
            require(review.get("passed") is True, "The final rendered sequence still has unresolved review findings.")
            report["comparison_complete"] = not report["requirements_unmet"]
            report["status"] = "completed_sampled_checks" if report["comparison_complete"] else "rendered_needs_review"
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            _write(destination, report)
            print(f"Comparison video: {video}\nEvidence: {destination}", flush=True)
            return 0 if report["comparison_complete"] else 2
    except Exception as exc:
        report["status"] = "blocked"
        report["blocked_stage"] = locals().get("active_stage", "configuration")
        report["error"] = _error(exc)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write(destination, report)
        print(f"Comparison blocked at {report['blocked_stage']}: {report['error']['message']}\nEvidence: {destination}", flush=True)
        return 2
    finally:
        state.clear()
        state.update(previous_state)
        stop_event.set() if was_stopped else stop_event.clear()
        job_lock.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-only", action="store_true", help="Run real narration, planning and sound; prepare HTML without encoding")
    mode.add_argument("--render", action="store_true", help="Encode and review the complete comparison (default)")
    mode.add_argument("--export-checkpoint", type=Path, default=None,
                      help="Export verified raw narration, measured inputs and assets from --output to this NEW directory, then exit without inference")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / ".smoke" / "comparable-armour")
    parser.add_argument("--profile", default=None, help="Existing channel profile ID; defaults to the active profile")
    parser.add_argument("--local-model", default=None, help="Use an installed local model for this run only; never use a hosted fallback")
    parser.add_argument("--local-url", default=None, help="Loopback Ollama address for --local-model; otherwise use the configured address")
    parser.add_argument("--resume", action="store_true", help="Reuse verified recordings/assets from this output folder; rerun direction and review")
    parser.add_argument("--resume-from-checkpoint", type=Path, default=None,
                        help="Verify and restore a checkpoint to a NEW --output, then resume with normal fresh direction, sound and review requests")
    parser.add_argument("--replay-visual-responses", type=Path, default=None,
                        help="Explicitly reapply a sealed local-response binding through current validators; requires --resume and unchanged inputs/model/prompt/schema; sound and vision still run normally")
    parser.add_argument("--voice-attempts", type=int, choices=range(1, 6), default=4)
    parser.add_argument("--use-cached-voice", action="store_true", help="Allow the production voice cache; provenance records any reuse")
    parser.add_argument("--max-repairs", type=int, choices=(0, 1, 2), default=1)
    parser.add_argument("--allow-technical-fallback", action="store_true",
                        help="Retain a clearly labelled incomplete demonstration when a model or semantic review is unavailable")
    args = parser.parse_args()
    try:
        if args.export_checkpoint is not None:
            if args.resume_from_checkpoint is not None or args.replay_visual_responses is not None or args.resume:
                raise ComparisonBlocked("Checkpoint export is a standalone read of --output; do not combine it with resume or replay.")
            manifest = _export_checkpoint(args.output, args.export_checkpoint)
            print(f"Verified checkpoint: {args.export_checkpoint}\nRetained {len(manifest['files'])} files; no model, narration or rendering calls were made.", flush=True)
            return 0
        return run(args)
    except (OSError, ValueError, ComparisonBlocked) as exc:
        print(f"Comparison cannot start: {_error(exc)['message']}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
