"""Only audio actually applied to the current recording receives project credits."""
import json

import config
from studio.description import build_description, load_audio_credits


def test_audio_credits_follow_the_recording_and_preserve_imported_source(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    manifest = {"version": 1, "audio_sha256": "current-recording", "path": "narration_mixed.wav", "assets": [{
        "id": "a" * 32, "kind": "music", "title": "Calm piano", "filename": "calm.mp3",
        "creator": "Example musician", "source_url": "https://pixabay.com/music/example/",
        "license_note": "Download reference kept locally", "content_id": "registered",
        "mode": "bed", "volume": .08,
    }]}
    path = tmp_path / "audio_credits.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "narration_mixed.wav").write_bytes(b"prepared soundtrack")
    (tmp_path / "render_audio.json").write_text(json.dumps({
        "path": "narration_mixed.wav", "audio_sha256": "current-recording",
    }), encoding="utf-8")
    timeline = {"audio_sha256": "current-recording"}
    assets = load_audio_credits(timeline)
    assert assets == manifest["assets"]
    text = build_description({"title": "A quiet topic", "sections": [], "tags": []}, {}, timeline=timeline)
    assert "Music: Calm piano · Example musician · https://pixabay.com/music/example/" in text
    assert load_audio_credits({"audio_sha256": "different-recording"}) == []
    stale_text = build_description({"sections": []}, {}, timeline={"audio_sha256": "different-recording"})
    assert "Calm piano" not in stale_text
    # Losing the mix or falling back to clean voice cannot claim the old music.
    (tmp_path / "narration_mixed.wav").unlink()
    assert load_audio_credits(timeline) == []
    (tmp_path / "narration_mixed.wav").write_bytes(b"old soundtrack still present")
    (tmp_path / "narration.wav").write_bytes(b"clean narration")
    (tmp_path / "render_audio.json").write_text(json.dumps({
        "path": "narration.wav", "audio_sha256": "current-recording",
    }), encoding="utf-8")
    assert load_audio_credits(timeline) == []


def test_audio_credit_metadata_does_not_expose_paths_or_unsafe_links(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    path = tmp_path / "audio_credits.json"
    (tmp_path / "narration_effects.wav").write_bytes(b"prepared soundtrack")
    (tmp_path / "render_audio.json").write_text(json.dumps({
        "path": "narration_effects.wav", "audio_sha256": "current-recording",
    }), encoding="utf-8")
    path.write_text(json.dumps({"version": 1, "path": "narration_effects.wav", "audio_sha256": "current-recording", "assets": [
        {"kind": "sfx", "title": "Wind", "path": "/private/source.wav",
         "source_url": "javascript:alert(1)", "volume": 3},
        {"kind": "music", "source_url": "https://user:password@example.com/private"},
        {"kind": "other", "title": "Ignore me"},
    ]}), encoding="utf-8")
    assets = load_audio_credits()
    assert len(assets) == 2
    assert all(row["source_url"] == "" and "path" not in row for row in assets)
    assert "volume" not in assets[0]
    path.write_text("{broken", encoding="utf-8")
    assert load_audio_credits() == []
