"""Reproduce the silent, real-photo README demos without API keys or TTS.

Run from the repository root:
    python renderers/hyperframes/demo.py
    python renderers/hyperframes/demo.py --portrait

The authored 18-second timeline uses the same fixed templates and HyperFrames
adapter as the application. Its soundtrack is an explicitly zero-filled WAV.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
from video.renderers.html import prepare_motion_project, render_motion_video


SOURCE_IMAGE = ROOT / "docs" / "samples" / "skylab.jpg"
SOURCE_URL = "https://images.nasa.gov/details/sl4-143-4707"
DURATION = 18
FPS = 24


def build_demo(output: Path, portrait: bool = False) -> tuple[dict, Path]:
    """Write reproducible local inputs; all scene times are explicitly authored."""
    if not SOURCE_IMAGE.is_file():
        raise FileNotFoundError("The bundled NASA source photograph is missing: docs/samples/skylab.jpg")
    output.mkdir(parents=True, exist_ok=True)
    picture = output / "skylab.jpg"
    shutil.copy2(SOURCE_IMAGE, picture)
    audio = output / "silent.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b"\x00\x00" * 48000 * DURATION)

    scenes = [
        ("text", "Reuse your pictures", ["Existing photographs", "Purposeful motion"],
         "Existing photographs, with a little movement."),
        ("flow", "Keep your existing recording", ["Saved recording", "Subtitle timing", "Scenes in sync"],
         "Use subtitles when available. Estimated timing stays labelled."),
        ("flow", "Edit one scene, render again", ["Choose a scene", "Refine its look", "Render again"],
         "Change the scene while your narration stays in place."),
    ]
    width, height = (720, 1280) if portrait else (1280, 720)
    plan = {
        "version": 1, "title": "YAAS · SILENT VISUAL DEMO", "mode": "motion", "fps": FPS,
        "width": width, "height": height, "duration": DURATION, "audio_duration": DURATION,
        "timing_source": "authored_silent_demo", "timing_precision": "authored",
        "warnings": ["Silent visual demonstration: scene timings are authored and the soundtrack contains digital silence."],
        "sections": [], "scenes": [], "captions": [],
    }
    for index, (template, title, labels, caption) in enumerate(scenes):
        start = index * 6
        reveals = [.9, 2.2, 3.7] if template == "flow" else [.8, 1.65]
        beats = [{"time": .06, "target": "title", "action": "reveal"}]
        for item, when in enumerate(reveals):
            beats.append({"time": when, "target": f"item-{item}", "action": "reveal"})
            if template == "flow" and item:
                beats.append({"time": when + .18, "target": f"item-{item}", "action": "connect"})
        plan["scenes"].append({
            "id": f"demo-{index + 1}", "section_id": str(index + 1), "template": template,
            "start": start, "duration": 6, "title": title,
            "visual_goal": "Demonstrate the picture and editing workflow with calm, authored motion.",
            "items": [{"label": label} for label in labels], "beats": beats,
            "image": {"path": str(picture), "source": "NASA", "credit": "Photograph: NASA / JSC",
                      "source_url": SOURCE_URL, "license": "NASA Images and Media Usage Guidelines"},
        })
        plan["sections"].append({"id": str(index + 1), "start": start, "duration": 6, "narration": ""})
        plan["captions"].append({"start": start + .55, "end": start + 5.75, "text": caption})
    (output / "scene_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan, audio


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portrait", action="store_true", help="Render the 720 × 1280 portrait demonstration")
    parser.add_argument("--prepare-only", action="store_true", help="Create the local HTML project without encoding video")
    args = parser.parse_args()
    orientation = "portrait" if args.portrait else "landscape"
    output = Path(__file__).parent / ".smoke" / f"demo-{orientation}"
    plan, audio = build_demo(output.resolve(), args.portrait)
    entry = prepare_motion_project(plan, str(audio), str(output))
    print(f"Prepared {entry}", flush=True)
    if args.prepare_only:
        return
    # This standalone demo uses a fixed quality setting and a conservative
    # worker count, independent of the user's channel configuration.
    config.MOTION_QUALITY = "standard"
    config.MOTION_WORKERS = 1
    rendered = render_motion_video({"title": plan["title"]}, str(audio), str(output), plan,
                                    video_type="shorts" if args.portrait else "normal")
    target = ROOT / "docs" / "samples" / f"motion-{orientation}.mp4"
    temporary = target.with_suffix(".new.mp4")
    shutil.copy2(rendered, temporary)
    temporary.replace(target)
    print(f"Saved silent visual demo: {target}", flush=True)
    if not args.portrait:
        poster = ROOT / "docs" / "screenshots" / "motion-photo-demo.png"
        poster.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", "10.6", "-i", str(target), "-frames:v", "1", "-update", "1", str(poster)],
                       check=True, timeout=60)
        print(f"Saved poster: {poster}", flush=True)


if __name__ == "__main__":
    main()
