"""Reproduce scene direction through the real local planner, without providers.

This is a qualitative, hypothetical illustration of homes adapting to water;
it is not a geographical forecast or an engineering design. The 14-second clock
and phrase cues are authored, not measured speech. No narration, downloaded
photos, stock search, or inference is claimed by this fixture.

    python renderers/hyperframes/directed_demo.py --prepare-only
    python renderers/hyperframes/directed_demo.py --portrait
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agents.visual_director import plan_scenes
from video.renderers.html import prepare_motion_project, render_motion_video


def fixture(output, portrait=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    script = {"title": "Living with rising water", "video_type": "shorts" if portrait else "normal", "sections": [
        {"id": "coast", "title": "When water reaches home", "duration_seconds": 4,
         "narration": "Rising floodwater reaches homes on the coast.",
         "bullet_points": ["Floodwater rises", "Coastal homes"]},
        {"id": "adapt", "title": "Homes that rise with water", "duration_seconds": 6,
         "narration": "Floating homes rise with water as communities adapt.",
         "bullet_points": ["Floating homes rise with water", "People adapt their buildings"]},
        {"id": "power", "title": "Power moves through the system", "duration_seconds": 4,
         "narration": "Solar panels feed batteries that supply power to homes.",
         "bullet_points": ["Solar panels supply energy", "Batteries store energy", "Homes receive power"]},
    ]}
    timeline = {"version": 1, "fps": 12, "width": 360 if portrait else 640,
                "height": 640 if portrait else 360, "duration": 14,
                "sections": [], "captions": [], "words": []}
    start = 0
    for row in script["sections"]:
        duration = row["duration_seconds"]
        timeline["sections"].append({"id": row["id"], "start": start, "duration": duration,
                                     "caption_timing": "phrase"})
        timeline["captions"].append({"start": start + .15, "end": start + duration - .15,
                                     "text": row["narration"]})
        start += duration
    plan = plan_scenes(script, timeline, use_ai=False, use_project_photos=False)
    audio = output / "silent-clock.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(bytes(14 * 48000 * 2))
    for name, value in (("script.json", script), ("timeline.json", timeline), ("scene_plan.json", plan)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return script, plan, audio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portrait", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = (args.output or Path(__file__).parent / ".smoke" / ("directed-portrait" if args.portrait else "directed-landscape")).resolve()
    script, plan, audio = fixture(output, args.portrait)
    print(prepare_motion_project(plan, str(audio), str(output)), flush=True)
    if not args.prepare_only:
        print(render_motion_video(script, str(audio), str(output), plan,
                                  video_type="shorts" if args.portrait else "normal", preview=True,
                                  progress=lambda message: print(message, flush=True)))


if __name__ == "__main__":
    main()
