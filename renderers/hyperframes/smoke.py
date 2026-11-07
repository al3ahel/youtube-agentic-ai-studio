"""Offline fixture: exercise every template, narration timing and local footage.

Run from the repository root:
    python renderers/hyperframes/smoke.py --prepare-only
    python renderers/hyperframes/smoke.py
    python renderers/hyperframes/smoke.py --portrait

No API keys or TTS service are used. The soundtrack, raster picture, chart and
video pattern are explicitly synthetic fixtures rather than stock media.
"""
from __future__ import annotations

import argparse
import array
import json
import math
from pathlib import Path
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from video.renderers.html import prepare_motion_project, render_motion_video


def fixture(output: Path, portrait=False, seconds=3.0) -> tuple[dict, Path]:
    from PIL import Image, ImageDraw

    output.mkdir(parents=True, exist_ok=True)
    width, height = (360, 640) if portrait else (640, 360)
    descriptions = [
        ("text", "An idea should move.", ["A story", "A clear picture", "A voice to follow"]),
        ("flow", "From question to understanding", ["Ask a question", "Follow the evidence", "Connect the pieces", "Explain the answer"]),
        ("comparison", "Different views. The same question.", ["See the whole system", "Examine one detail"]),
        ("timeline", "Every explanation has a sequence", ["Start with context", "Make the connection", "Reach the insight"]),
        ("chart", "Example values · synthetic fixture", ["Example A", "Example B", "Example C"]),
        ("explain", "Give the idea a structure", ["The core idea", "Evidence", "Connections", "Consequences", "Questions"]),
        ("footage", "Bring the explanation into the world", ["Local footage", "The same narration clock"]),
    ]
    duration = len(descriptions) * seconds
    audio = output / "narration.wav"
    samples = array.array("h")
    for index in range(round(duration * 48000)):
        t = index / 48000
        envelope = min(1, (t % seconds) * 10) * min(1, (seconds - t % seconds) * 10)
        frequency = [220, 261.63, 329.63, 293.66, 349.23, 392, 440][min(6, int(t / seconds))]
        samples.append(round(1700 * envelope * math.sin(2 * math.pi * frequency * t)))
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(samples.tobytes())
    footage = output / "fixture-footage.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=size=640x360:rate=12", "-t", str(seconds + .2), "-an", "-c:v", "libx264",
        "-threads", "1", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(footage),
    ], check=True, timeout=30)
    # A reproducible local raster exercises the same photo import, cropping and
    # narration-clock camera motion as downloaded pictures, without a network.
    picture = output / "fixture-picture.jpg"
    raster = Image.new("RGB", (1600, 1000), "#8daab3")
    drawing = ImageDraw.Draw(raster)
    drawing.ellipse((1040, 110, 1300, 370), fill="#e9d2a3")
    drawing.polygon([(0, 700), (410, 290), (900, 850), (1600, 410), (1600, 1000), (0, 1000)], fill="#526e72")
    drawing.polygon([(0, 850), (540, 660), (850, 900), (1310, 540), (1600, 750), (1600, 1000), (0, 1000)], fill="#243e46")
    for x in range(80, 1550, 230):
        drawing.line([(x, 1000), (x + 150, 800)], fill="#bed1c2", width=4)
    raster.save(picture, quality=92)
    plan = {
        "version": 1, "title": "Ideas in motion", "mode": "hybrid", "fps": 12,
        "width": width, "height": height, "duration": duration,
        "sections": [], "captions": [], "scenes": [],
    }
    for index, (template, title, labels) in enumerate(descriptions):
        start = index * seconds
        items = [{"label": value} for value in labels]
        if template == "chart":
            for item, value in zip(items, [2, 5, 3]):
                item.update(value=value, source="Synthetic fixture values; not real-world measurements")
        scene = {
            "id": f"scene-{index}", "section_id": f"section-{index}", "template": template,
            "start": start, "duration": seconds, "title": title,
            "visual_goal": "Exercise the fixed template with supplied data and exact timing.",
            "items": items,
            "beats": [{"time": .05, "target": "title", "action": "reveal"}] + [
                {"time": .35 + item_index * min(.35, seconds / (len(items) + 2)),
                 "target": f"item-{item_index}", "action": "reveal"}
                for item_index in range(len(items))
            ],
        }
        if template == "footage":
            scene["footage"] = {"path": str(footage), "source_url": "", "credit": "Local FFmpeg test pattern · offline fixture", "in_point": 0}
        elif template != "chart":
            scene["image"] = {"path": str(picture), "source": "Offline fixture",
                              "credit": "Synthetic picture · renderer test fixture", "source_url": "", "license": ""}
        if template == "flow":
            scene["beats"].extend({"time": .35 + item_index * min(.35, seconds / (len(items) + 2)),
                                   "target": f"item-{item_index}", "action": "connect"}
                                  for item_index in range(1, len(items)))
        if template == "chart":
            scene["beats"].extend({"time": .35 + item_index * min(.35, seconds / (len(items) + 2)),
                                   "target": f"item-{item_index}", "action": "count"}
                                  for item_index in range(len(items)))
        if template == "timeline":
            scene["beats"].append({"time": .2, "target": "scene", "action": "draw"})
        plan["scenes"].append(scene)
        plan["sections"].append({"id": f"section-{index}", "start": start, "duration": seconds, "narration": title})
        plan["captions"].append({"start": start + .2, "end": start + seconds - .15, "text": title})
    return plan, audio


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portrait", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--seconds-per-scene", type=float, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.seconds_per_scene <= 30:
        parser.error("--seconds-per-scene must be between 1 and 30")
    output = (args.output or Path(__file__).parent / ".smoke" / ("portrait" if args.portrait else "landscape")).resolve()
    plan, audio = fixture(output, args.portrait, args.seconds_per_scene)
    (output / "scene_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    entry = prepare_motion_project(plan, str(audio), str(output))
    print(f"Prepared {entry}")
    if not args.prepare_only:
        print(render_motion_video({"title": plan["title"]}, str(audio), str(output), plan,
                                  video_type="shorts" if args.portrait else "normal", preview=True))


if __name__ == "__main__":
    main()
