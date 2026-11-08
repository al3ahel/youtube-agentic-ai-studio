"""Offline actor regression through the normal planner, not a narrated demo.

The source names equipment without specifying a firing event or outcome. The
local fallback must show those subjects instead of narration-fragment cubes.
The silent eight-second clock is synthetic. Both orientations and the saved
version-1 actor renderer are prepared for browser regression checks.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
from agents import object_direction, visual_director
from video.renderers.html import prepare_motion_project


def fixture(output, portrait=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    config.SHOT_PLANNING_ENABLED = True
    script = {"title": "Protection had limits", "video_type": "shorts" if portrait else "normal", "sections": [
        {"id": "test", "title": "One test had limits", "narration": "One successful test was no universal guarantee. Armour had limits.",
         "bullet_points": ["One successful test was", "No universal guarantee", "A specific test is not a"]},
        {"id": "equipment", "title": "Armour and firearms", "narration": "Armour and firearms evolved together; protection always had limits.",
         "bullet_points": ["A specific test is not a", "No universal guarantee", "An additional thought"]},
    ]}
    timeline = {"fps": 12, "width": 360 if portrait else 640, "height": 640 if portrait else 360,
                "duration": 8, "sections": [], "captions": [], "words": []}
    for index, section in enumerate(script["sections"]):
        timeline["sections"].append({"id": section["id"], "start": index * 4, "duration": 4, "caption_timing": "phrase"})
        timeline["captions"].append({"start": index * 4 + .15, "end": index * 4 + 3.85,
                                     "text": section["narration"]})
    plan = visual_director.plan_scenes(script, timeline, use_ai=False, mode="motion", use_project_photos=False)
    audio = output / "silent-clock.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(48000)
        stream.writeframes(bytes(8 * 48000 * 2))
    for name, value in (("script.json", script), ("timeline.json", timeline), ("scene_plan.json", plan)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    current = prepare_motion_project(plan, str(audio), str(output))
    legacy = copy.deepcopy(plan)
    for scene, section in zip(legacy["scenes"], script["sections"]):
        # Version 1 requires at least two items. Keep the one-subject scene
        # current, and exercise the real saved-version path on the two actors.
        if len(scene["items"]) < 2:
            continue
        scene.pop("shot", None); scene.pop("staging", None)
        for item in scene["items"]:
            item.pop("role", None)
        object_direction.apply_objects(scene, section["narration"])
    visual_director.validate_plan(legacy)
    previous = prepare_motion_project(legacy, str(audio), str(output.with_name(output.name + "-legacy")))
    return current, previous


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portrait", action="store_true")
    args = parser.parse_args()
    output = Path(__file__).parent / ".smoke" / ("subjects-portrait" if args.portrait else "subjects-landscape")
    for entry in fixture(output, args.portrait):
        print(entry)
