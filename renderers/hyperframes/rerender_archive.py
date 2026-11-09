"""Replan and review an archived comparison locally, preserving its entire AAC mix.

    python renderers/hyperframes/rerender_archive.py --local-model qwen3.5:4b

Uses installed Ollama and renderer dependencies only. No narration service,
stock search, hosted model, or audio mixer is called. This visual continuation
does not satisfy fresh narration/sound requirements or approve a comparison.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from renderers.hyperframes import comparable_demo as demo


def _ffmpeg(arguments):
    executable = shutil.which("ffmpeg")
    if not executable:
        raise demo.ComparisonBlocked("FFmpeg is required for local soundtrack preservation.")
    return subprocess.run([executable, "-v", "error", "-nostdin", *map(str, arguments)],
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)


def _pcm_signature(path):
    result = _ffmpeg(["-i", path, "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"])
    if not result.stdout:
        raise demo.ComparisonBlocked("The archive has no decodable complete soundtrack.")
    return {"sha256": hashlib.sha256(result.stdout).hexdigest(), "bytes": len(result.stdout),
            "format": "pcm_s16le at the source stream's sample rate and channel count"}


def _preserve_audio(video, archive_video, expected_pcm):
    """Stream-copy the complete original AAC track, without trimming or mixing."""
    video = Path(video)
    temporary = video.with_name(".archive-remux.mp4")
    try:
        _ffmpeg(["-y", "-i", video, "-i", archive_video, "-map", "0:v:0", "-map", "1:a:0",
                 "-c", "copy", "-map_metadata", "-1", "-movflags", "+faststart", temporary])
        actual = _pcm_signature(temporary)
        if actual != expected_pcm:
            raise demo.ComparisonBlocked("The remux changed the archived soundtrack; candidate was not accepted.")
        temporary.replace(video)
        return actual
    finally:
        temporary.unlink(missing_ok=True)


def _load_archive(source):
    from video.timeline import script_signature
    names = ("video.mp4", "script.json", "scene-plan.json", "reference.json", "comparison.json",
             "packaging-provenance.json", "museum-assets.json", "credits.json", "sound-design.json")
    paths = {name: source / name for name in names}
    if any(path.is_symlink() or not path.is_file() for path in paths.values()):
        raise demo.ComparisonBlocked("The archive is missing a regular source or provenance file.")
    hashes = {name: demo._sha(path) for name, path in paths.items()}
    data = {name: demo._read(path) for name, path in paths.items() if name.endswith(".json")}
    packaging, previous = data["packaging-provenance.json"], data["comparison.json"]
    if (hashes["video.mp4"] != packaging["video"]["sha256"]
            or paths["video.mp4"].stat().st_size != packaging["video"]["bytes"]
            or hashes["scene-plan.json"] != packaging["plan_path_normalization"]["portable_file_sha256"]
            or hashes["script.json"] != previous["protected_inputs"]["script.json"]
            or data["scene-plan.json"]["script_signature"] != script_signature(data["script.json"])):
        raise demo.ComparisonBlocked("The archive movie, script or measured plan does not match its published provenance.")
    return data, hashes


def _recover_clock(script, plan, mix):
    """Copy archived measured events; never invent a new raw narration manifest."""
    from video.timeline import script_signature
    timeline = deepcopy({key: plan[key] for key in ("fps", "width", "height", "duration", "sections", "captions", "words", "timing_source")})
    timeline.update(version=1, title=script["title"], script_signature=script_signature(script),
                    audio_duration=plan["audio_duration"], total_frames=round(plan["duration"] * plan["fps"]),
                    audio_sha256=demo._sha(mix), audio_origin="decoded_archived_complete_mix", narration_reused=True,
                    captions_source="archived_measured_scene_plan", source_timeline_signature=plan.get("timeline_signature"))
    timeline["timeline_signature"] = hashlib.sha256(json.dumps(timeline, sort_keys=True, ensure_ascii=False,
                                                               allow_nan=False).encode()).hexdigest()
    return timeline


def _restore_museum_assets(source, data, output):
    """Restore verified pictures and the normal adapter's flat credit sidecar.

    Published credits also contain audio attribution. Their exact original stays
    in archive_source/credits.json; only the museum rows enter runtime credits.
    """
    source, output = demo._checkpoint_root(source), demo._checkpoint_root(output)
    bundle = data["credits.json"]
    credits = bundle.get("museum_images") if isinstance(bundle, dict) else bundle
    if (not isinstance(credits, list) or not credits
            or not all(isinstance(row, dict) and isinstance(row.get("file"), str) for row in credits)
            or len({Path(row["file"]).name for row in credits}) != len(credits)):
        raise demo.ComparisonBlocked("Archived museum credits are missing or ambiguous.")
    by_file = {Path(row["file"]).name: row for row in credits}
    records = {row["object_id"]: row for row in data["museum-assets.json"] if row.get("status") == "acquired"}
    image_map, restored, copies = {}, [], []
    for item in data["reference.json"]["museum_assets"]:
        row = records[item["object_id"]]
        image = demo._checkpoint_file(source, row["file"])
        credit = by_file.get(image.name, {})
        if (row["file"] != f"images/met-{item['object_id']}.jpg" or row.get("is_public_domain") is not True
                or demo._sha(image) != row["sha256"] or row.get("object_url") != item["object_url"]
                or credit.get("link") != row["object_url"] or credit.get("license") != row.get("license")
                or not credit.get("source") or not row.get("title")
                or not str(credit.get("credit", "")).startswith(row["title"] + " · ")):
            raise demo.ComparisonBlocked("An archived museum image or its identity failed its acquisition binding.")
        copied = output / row["file"]
        demo._checkpoint_root(copied.parent)
        if copied.exists() or copied.is_symlink():
            if demo._sha(demo._checkpoint_file(output, row["file"])) != row["sha256"]:
                raise demo.ComparisonBlocked("An existing museum image differs from the archive; it was not replaced.")
        copies.append((image, copied))
        restored.append(deepcopy(credit))
        for section_id in item["section_ids"]:
            image_map.setdefault(str(section_id), []).append(str(copied))
    for image, copied in copies:
        copied.parent.mkdir(exist_ok=True)
        if not copied.exists():
            shutil.copyfile(image, copied)
    demo._write(output / "credits.json", restored)
    demo._write(output / "museum_assets.json", data["museum-assets.json"])
    demo._write(output / "image_map.json", {key: [Path(path).relative_to(output).as_posix() for path in paths]
                                           for key, paths in image_map.items()})
    return image_map


def _settings(args, output):
    import config
    from apis.local_llm import validate_base_url
    if not config._valid_setting("OLLAMA_MODEL", args.local_model) or not args.local_model:
        raise demo.ComparisonBlocked("Choose a genuinely installed local model.")
    settings = {"LLM_ROUTE": "local", "OLLAMA_MODEL": args.local_model,
                "OLLAMA_BASE_URL": validate_base_url(args.local_url or config.OLLAMA_BASE_URL),
                "VISUAL_AI_ENABLED": True, "VISUAL_REVIEW_ENABLED": True, "SHOT_PLANNING_ENABLED": True,
                "OUTPUT_DIR": str(output), "IMAGES_DIR": str(output / "images"), "OPENROUTER_FREE_ONLY": True}
    # Truthy disabled sentinels also prevent provider helpers falling back to
    # credentials in environment variables. Saved settings are never changed.
    settings.update({name + "_API_KEY": "YOUR_ARCHIVE_DISABLED" for name in ("GEMINI", "GROQ", "OPENROUTER", "PEXELS", "PIXABAY")})
    return settings


def run(args):
    import config
    from agents.visual_director import plan_scenes, has_model_visual_contribution
    from agents.render_review import review_and_repair
    from video.renderers.html import render_motion_video, prepare_motion_project
    source, output = demo._checkpoint_root(args.source), demo._checkpoint_root(args.output)
    if output.exists() or output.is_relative_to(source):
        raise demo.ComparisonBlocked("Choose a new output directory outside the source archive.")
    settings = _settings(args, output)
    output.mkdir(parents=True)
    report = {"version": 1, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(), "stages": {},
              "workflow": "fresh_local_visual_planning_and_review_with_archived_audio", "comparison_complete": False,
              "new_narration": False, "new_sound_inference": False, "new_sound_mix": False,
              "audio_origin": "Complete narration and automatic soundtrack from the published MP4; preserved without remixing.",
              "requirements_unmet": ["fresh_complete_narration", "fresh_automatic_sound_mix"],
              "reference_quality_parity_claimed": False, "subjective_audio_listening": "not_performed"}
    report_path, stop = output / "archive_rerender.json", threading.Event()

    def phase(name, value=None):
        report["active_stage"] = name
        if value is not None:
            report["stages"][name] = value
        demo._write(report_path, report)
        print(f"Archive visual continuation: {name}", flush=True)

    try:
        phase("archive_verification")
        data, hashes = _load_archive(source)
        script, target = data["script.json"], data["reference.json"]["target"]
        verified = demo._verify_encoded(source / "video.mp4", target)
        if verified["status"] != "verified":
            raise demo.ComparisonBlocked("The source movie does not match its recorded reference format.")
        archive = output / "archive_source"
        archive.mkdir()
        for name in data:
            shutil.copyfile(source / name, archive / name)
        report["source"] = {"files_sha256": hashes, "comparison_complete": data["comparison.json"].get("comparison_complete"),
                            "status": data["comparison.json"].get("status"), "gates": data["comparison.json"].get("gates"),
                            "timing": "Exact archived section, caption and word positions; no fresh timing measurement."}
        phase("archive_verification", verified)
        phase("archived_audio")
        original_pcm = _pcm_signature(source / "video.mp4")
        mix = output / "archived_complete_mix.wav"
        _ffmpeg(["-y", "-i", source / "video.mp4", "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", mix])
        timeline = _recover_clock(script, data["scene-plan.json"], mix)
        demo._write(output / "archived_timeline.json", timeline)
        demo._write(output / "script.json", script)
        image_map = _restore_museum_assets(source, data, output)
        phase("archived_audio", {"pcm": original_pcm, "decoded_mix_sha256": demo._sha(mix), "mixing_performed": False,
                                 "image_credits": {"file": "credits.json", "sha256": demo._sha(output / "credits.json"),
                                                   "origin": "Verified museum_images rows from archive_source/credits.json"}})
        with config.profile_context(settings), demo._capture_local_responses(output, lambda: report["active_stage"]) as captures:
            report["local_response_capture"] = captures
            phase("local_runtime", demo._local_runtime_evidence())
            if report["stages"]["local_runtime"].get("status") != "installed":
                raise demo.ComparisonBlocked("The selected local model is unavailable; no hosted fallback was attempted.")
            phase("visual_planning")
            plan = plan_scenes(script, timeline, mode="hybrid", output_dir=str(output), use_ai=True,
                               image_map=image_map, use_project_photos=True, stop_event=stop, profile_id="default")
            demo._write(output / "scene_plan.json", plan)
            phase("visual_planning", {"ai_status": plan.get("ai_status"), "shot_count": len(plan["scenes"]),
                                      "new_inference": True, "shot_report": plan.get("shot_report")})
            if plan.get("ai_status", {}).get("status") != "planned" or not has_model_visual_contribution(plan):
                raise demo.ComparisonBlocked("Fresh local planning retained no verified model contribution.")

            def render(candidate):
                phase("encoding")
                video = render_motion_video(script, str(mix), str(output), candidate, video_type="shorts",
                                            stop_event=stop, progress=lambda text: print(text, flush=True))
                _preserve_audio(video, source / "video.mp4", original_pcm)
                phase("render_review")
                return video

            video = render(plan)
            video, plan, review = review_and_repair(video, plan, str(output), render=render, script=script,
                                                   max_repairs=args.max_repairs, stop_event=stop,
                                                   progress=lambda text: print(text, flush=True))
            demo._write(output / "scene_plan.json", plan)
            demo._write(output / "visual_qa.json", review)
            prepare_motion_project(plan, str(mix), str(output))
            phase("render_review", review)
            phase("final_verification")
            final_pcm, encoded = _pcm_signature(video), demo._verify_encoded(video, target)
            if final_pcm != original_pcm or encoded["status"] != "verified":
                raise demo.ComparisonBlocked("The final movie failed exact soundtrack or target-format verification.")
            phase("final_verification", {"encoded": encoded, "audio_pcm": final_pcm, "audio_pcm_identical": True})
            report.update(video=str(Path(video).relative_to(output)), visual_review_passed=review.get("passed") is True,
                          status="rendered_visual_review_passed_audio_reused" if review.get("passed") is True else "rendered_needs_review")
            if review.get("passed") is not True:
                report["requirements_unmet"].append("render_review_passed")
            return 0 if review.get("passed") is True else 2
    except Exception as exc:
        report.update(status="blocked", blocked_stage=report.get("active_stage"), error=demo._error(exc))
        print(f"Archive continuation blocked: {report['error']['message']}", flush=True)
        return 2
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        demo._write(report_path, report)
        print(f"Evidence: {report_path}\nFresh narration and automatic sound remain pending; comparison_complete=false.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=ROOT / "docs/samples/comparable-armour")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / ".smoke/comparable-armour-repaired")
    parser.add_argument("--local-model", default="qwen3.5:4b")
    parser.add_argument("--local-url", default=None)
    parser.add_argument("--max-repairs", type=int, choices=(0, 1, 2), default=1)
    try:
        return run(parser.parse_args())
    except (OSError, ValueError, demo.ComparisonBlocked) as exc:
        print(demo._error(exc)["message"], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
