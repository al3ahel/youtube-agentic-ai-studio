"""Reproducible 14-second object-animation proof, without external providers.

The source is a qualitative explanation of census records and ore extraction,
not a claim about a particular country, mine or population. Timings are authored
phrase cues, not measured speech. A silent WAV lets the real encoder verify the
same narration-clock/media path; a licensed soundtrack may be mixed separately.

    python renderers/hyperframes/objects_demo.py --prepare-only
    python renderers/hyperframes/objects_demo.py --portrait
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
from agents.object_direction import apply_objects
from video.renderers.html import prepare_motion_project, render_motion_video


def fixture(output, portrait=False):
    output.mkdir(parents=True, exist_ok=True)
    script = {"title": "Records and resources", "sections": [
        {"id": "census", "title": "Who enters the records?", "duration_seconds": 7,
         "narration": "A census records people. Missing census records can lead to an undercount.",
         "bullet_points": ["People enter census records", "Census undercounts leave gaps"]},
        {"id": "mining", "title": "From rock to extracted ore", "duration_seconds": 7,
         "narration": "Mining extracts ore from rock. A drill reaches the ore and a conveyor moves it.",
         "bullet_points": ["Mining drills reach the ore", "Ore moves along the conveyor"]},
    ]}
    timeline = {"version": 1, "fps": 12, "width": 360 if portrait else 640,
                "height": 640 if portrait else 360, "duration": 14,
                "sections": [{"id": row["id"], "start": index * 7, "duration": 7, "caption_timing": "phrase"}
                             for index, row in enumerate(script["sections"])], "captions": [], "words": []}
    for index, row in enumerate(script["sections"]):
        first, second = row["narration"].split(". ", 1)
        timeline["captions"].extend([
            {"start": index * 7, "end": index * 7 + 3.5, "text": first + "."},
            {"start": index * 7 + 3.5, "end": index * 7 + 7, "text": second},
        ])
    plan = plan_scenes(script, timeline, use_ai=False, use_project_photos=False)
    plan.pop("creative_brief", None)
    # A consistent neutral editorial treatment isolates the structure change.
    plan["visual_direction"] = {"version": 1, "look": "editorial", "rhythm": "measured",
                                "rationale": "Qualitative illustrated explanation, not a country-specific report."}
    for scene in plan["scenes"]:
        scene["composition"] = "split"
        # Keep this published fixture as a v1 compatibility proof. The separate
        # directed_demo exercises the current planner's composed scene worlds.
        section = next(row for row in script["sections"] if row["id"] == scene["section_id"])
        scene.pop("staging", None)
        scene.pop("palette", None)
        scene["template"] = "explain"
        scene["items"] = [{"label": label} for label in section["bullet_points"]]
        scene["beats"] = [{"time": 0, "target": "title", "action": "reveal"}]
        for index in range(len(scene["items"])):
            scene["beats"].extend([
                {"time": index * 3.5, "target": f"item-{index}", "action": "reveal"},
                {"time": index * 3.5 + .84, "target": f"item-{index}", "action": "highlight"},
            ])
        apply_objects(scene, section["narration"])
        for item in scene["items"]:
            item.pop("role", None)
    audio = output / "silent-clock.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(48000)
        stream.writeframes(bytes(14 * 48000 * 2))
    (output / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "scene_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return script, plan, audio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portrait", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = (args.output or Path(__file__).parent / ".smoke" / ("objects-portrait" if args.portrait else "objects-landscape")).resolve()
    script, plan, audio = fixture(output, args.portrait)
    print(prepare_motion_project(plan, str(audio), str(output)), flush=True)
    if not args.prepare_only:
        print(render_motion_video(script, str(audio), str(output), plan,
                                  video_type="shorts" if args.portrait else "normal", preview=True,
                                  progress=lambda message: print(message, flush=True)))


if __name__ == "__main__":
    main()
