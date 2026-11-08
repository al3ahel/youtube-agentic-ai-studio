"""Add real licensed sound to the illustrated renderer example.

Run objects_demo.py first. This listening sample has no voice: its bed/effect
balance is explicitly authored for music-only playback, not a speech-mix preset.
The production mixers, onset preparation, mastering and exact video clock are
used. No inference, network request, synthetic tone or external download occurs.

    python renderers/hyperframes/objects_sound_demo.py --input .smoke/objects-landscape
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from studio.description import _audio_credit_block
from video.audio_master import master_soundtrack
from video.music import mix_music_with_narration
from video.sound_effects import mix_sound_events


def build(source: Path):
    source = source.resolve()
    video = source / "motion_preview.mp4"
    clock = source / "silent-clock.wav"
    plan = json.loads((source / "scene_plan.json").read_text(encoding="utf-8"))
    if plan.get("duration") != 14 or plan.get("fps") != 12:
        raise ValueError("Use the 14-second, 12fps objects_demo.py source.")
    if not video.is_file() or not clock.is_file():
        raise FileNotFoundError("Encode objects_demo.py before adding its soundtrack.")
    catalog_root = ROOT / "assets" / "audio" / "bundled"
    catalog = json.loads((catalog_root / "catalog.json").read_text(encoding="utf-8"))
    names = ["music/cylinder-one-45s.ogg", "sfx/book-page-turn.ogg", "sfx/small-whoosh.ogg"]
    assets = [next(row for row in catalog["assets"] if row["filename"] == name) for name in names]
    for row in assets:
        data = (catalog_root / row["filename"]).read_bytes()
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("The licensed recording does not match its catalogue.")
    bed = source / "licensed-bed.wav"
    mixed = source / "licensed-mix.wav"
    # There is no narration in this example. These listening-demo ratios do not
    # replace the voice-relative levels selected by the production sound agent.
    result = mix_music_with_narration(clock, bed, music_volume=.35,
                                      track_path=catalog_root / names[0])
    if Path(result) != bed:
        raise RuntimeError("The listening example needs its real music bed.")
    events = [
        {"path": str(catalog_root / names[1]), "start": 3.5, "duration": 1.7, "volume": .75},
        {"path": str(catalog_root / names[2]), "start": 7.0, "duration": .19, "volume": .75},
    ]
    result = mix_sound_events(clock, mixed, events, soundtrack_path=bed, auto_level=True)
    if Path(result) != mixed:
        raise RuntimeError("The listening example needs both real timed effects.")
    mastered, report = master_soundtrack(clock, mixed, source / "licensed-master.wav")
    output = source / "illustrated-motion.mp4"
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-nostdin", "-y", "-v", "error",
                    "-i", str(video), "-i", mastered, "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", "14",
                    "-movflags", "+faststart", str(output)], check=True, timeout=60)
    credits = _audio_credit_block(assets).replace(
        "faded and mixed beneath narration.", "faded and mixed into this demonstration soundtrack.")
    (source / "audio-credits.txt").write_text(credits + "\n", encoding="utf-8")
    verification = {"purpose": "Illustrated graphics and licensed audio listening sample; no narration",
                    "clock": "Authored phrase cues; not measured speech alignment", "duration": 14, "fps": 12,
                    "music": assets[0]["title"], "events": [{"title": asset["title"], "start": event["start"]}
                                                            for asset, event in zip(assets[1:], events)],
                    "mix": "Music-only demo ratios: bed 0.35, calibrated effects 0.75; production voice ratios differ",
                    "mastering": report, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
    (source / "listening-sample.json").write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")
    print(output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    build(parser.parse_args().input)
