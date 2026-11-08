"""Renderer-only shot regression fixture; pictures, footage and clock are synthetic.

This is not the narrated reference comparison. It exercises deterministic 2D,
3D, old photo assets, collage and footage through the actual local composition
builder in both orientations, including reverse/suppressed-event seeks.
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
from video.renderers.html import prepare_motion_project
from smoke import fixture as media_fixture


def fixture(output: Path, portrait=False):
    from PIL import Image, ImageDraw

    _, _ = media_fixture(output, portrait, seconds=4)
    paths = [output / "fixture-picture.jpg"]
    for number, color in enumerate(("#8d6147", "#45646e"), 2):
        path = output / f"fixture-picture-{number}.png"
        picture = Image.new("RGB", (1200, 900), color)
        draw = ImageDraw.Draw(picture)
        draw.rounded_rectangle((220, 100, 980, 800), radius=30, fill="#e8ddc3")
        for i in range(5):
            draw.rectangle((290, 180 + i * 105, 390, 245 + i * 105), fill="#38636a")
            draw.line((450, 205 + i * 105, 870, 205 + i * 105), fill="#9c815c", width=18)
        draw.ellipse((860, 700, 1110, 950), fill="#d0a06c")
        picture.save(path)
        paths.append(path)
    source = lambda path: {"path": str(path), "source": "synthetic_fixture", "source_url": "",
                           "credit": "Original test fixture", "license": "Repository license"}
    specs = [
        ("establish", "wide", "stage2d", "armour", "steel", "reveal", "A material has a shape"),
        ("detail", "close", "diagram3d", "armour", "steel", "impact", "Contact changes the surface"),
        ("interaction", "medium", "diagram3d", "armour", "steel", "deflect", "Direction changes at contact"),
        ("interaction", "macro", "diagram3d", "armour", "mail", "penetrate", "Links separate locally"),
        ("detail", "medium", "diagram3d", "layers", "mixed", "separate", "Each layer has a role"),
        ("payoff", "wide", "stage2d", "layers", "fiber", "absorb", "The backing deforms"),
        ("establish", "wide", "collage", None, None, None, "Pictures can share a composition"),
        ("interaction", "medium", "footage", None, None, None, "Footage stays on the narration clock"),
        ("detail", "close", "stage2d", None, None, None, "Saved pictures still work"),
        ("payoff", "wide", "stage2d", None, None, None, "Energy moves through a system"),
    ]
    plan = {"version": 1, "title": "Shot renderer regression", "mode": "hybrid", "fps": 12,
            "width": 360 if portrait else 640, "height": 640 if portrait else 360,
            "duration": len(specs) * 4, "sections": [], "scenes": [], "captions": [], "words": []}
    for i, (role, framing, technique, kind, material, action, title) in enumerate(specs):
        scene = {"id": f"shot-{i}", "section_id": f"section-{i}", "start": i * 4, "duration": 4,
                 "template": "footage" if technique == "footage" else "explain", "title": title,
                 "visual_goal": "Synthetic renderer fixture; no model or narration inference is claimed.",
                 "items": [{"label": label} for label in ("Strike face", "Intermediate layer", "Fibre backing")],
                 "beats": [{"time": .05, "target": "title", "action": "reveal"},
                           {"time": 2, "target": "scene", "action": "highlight"}],
                 "shot": {"version": 1, "role": role, "framing": framing, "technique": technique,
                          "camera": "orbit" if technique == "diagram3d" else "push",
                          "transition": "dissolve" if i == 5 else "cut", "focus": title, "action": title}}
        if kind:
            scene["mechanism"] = {"version": 1, "kind": kind, "material": material, "action": action}
        if technique == "collage":
            scene["image"] = source(paths[0]); scene["media_layers"] = [source(path) for path in paths]
        elif technique == "footage":
            scene["footage"] = {"path": str(output / "fixture-footage.mp4"), "in_point": 0,
                                "credit": "Synthetic test pattern", "source_url": ""}
            scene["image"] = source(paths[1])
        elif i == 8:
            scene["image"] = source(paths[0])
        elif i == 9:
            scene["graphics_version"] = 2
            scene["staging"] = {"version": 1, "world": "generic", "action": "connect", "camera": "follow"}
            scene["items"] = [{"label": label, "symbol": symbol, "role": "subject" if j == 0 else "support"}
                              for j, (label, symbol) in enumerate((("Solar panels", "solar"), ("Battery", "battery"), ("Homes", "building")))]
            scene["beats"] += [{"time": .2 + j * .7, "target": f"item-{j}", "action": "reveal"} for j in range(3)]
        plan["scenes"].append(scene)
        plan["sections"].append({"id": scene["section_id"], "start": i * 4, "duration": 4, "narration": title})
        plan["captions"].append({"start": i * 4 + .15, "end": i * 4 + 3.8, "text": "Synthetic fixture · qualitative mechanism"})
    audio = output / "silent-clock.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(48000)
        stream.writeframes(bytes(plan["duration"] * 48000 * 2))
    (output / "scene_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return prepare_motion_project(copy.deepcopy(plan), str(audio), str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portrait", action="store_true")
    args = parser.parse_args()
    output = Path(__file__).parent / ".smoke" / ("shots-portrait" if args.portrait else "shots-landscape")
    print(fixture(output.resolve(), args.portrait))
