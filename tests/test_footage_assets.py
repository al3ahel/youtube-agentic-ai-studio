"""Credits follow used clips, not the history of provider downloads."""
import json

import config
from studio import description
from studio.footage_assets import credits_for_plan, current_video_credits


def _clip():
    return {"path": "/project/assets/videos/flood.mp4", "in_point": 0,
            "source_url": "https://pixabay.com/videos/id-123/", "credit": "Video by River on Pixabay",
            "selection": {"author": "River", "provider": "pixabay", "provider_id": "123",
                          "license": "Pixabay Content License", "license_url": "https://pixabay.com/service/license-summary/"}}


def test_unused_search_results_are_neither_credited_nor_resurrected(tmp_path):
    (tmp_path / "video_credits.json").write_text(json.dumps([
        {"provider": "pexels", "source": "Pexels video", "path": "/old/unrelated.mp4",
         "credit": "Unused clip", "source_url": "https://pexels.com/video/999/"}]), encoding="utf-8")
    plan = {"scenes": [{"id": "scene-1", "template": "footage", "footage": _clip()}]}
    (tmp_path / "scene_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    credits = current_video_credits(tmp_path)
    assert len(credits) == 1 and credits[0]["provider"] == "pixabay"
    assert "unrelated" not in json.dumps(credits)
    plan["scenes"][0].pop("footage")
    (tmp_path / "scene_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    assert current_video_credits(tmp_path) == []
    (tmp_path / "scene_plan.json").write_text("{broken", encoding="utf-8")
    assert current_video_credits(tmp_path) == []


def test_pixabay_source_and_license_are_in_description(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    plan = {"scenes": [{"template": "footage", "footage": _clip()}]}
    (tmp_path / "scene_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    credits = description.load_credits()
    text = "\n".join(description._credit_lines(credits))
    assert "Pixabay video" in text and "Video by River on Pixabay" in text
    assert "https://pixabay.com/videos/id-123/" in text
    assert "Pixabay Content License" in text and "https://pixabay.com/service/license-summary/" in text


def test_legacy_clip_reference_is_preserved_and_duplicate_use_is_credited_once():
    clip = _clip()
    clip.pop("selection")
    clip.update(source_url="https://www.pexels.com/video/example-1/", credit="Video by Example on Pexels")
    plan = {"scenes": [{"template": "process_flow", "footage": clip}, {"template": "footage", "footage": clip}]}
    assert len(credits_for_plan(plan)) == 1
    assert credits_for_plan(plan)[0]["source"] == "Pexels video"


def test_provider_card_exposes_video_key_when_photo_fallback_is_disabled(monkeypatch):
    from studio.dashboard import _providers
    monkeypatch.setattr(config, "PIXABAY_API_KEY", "fixture-valid-key")
    monkeypatch.setattr(config, "IMAGE_SOURCES", [])
    pixabay = next(row for row in _providers() if row["id"] == "pixabay")
    assert pixabay["ready"] is True and pixabay["role"] == "Stock photos + videos"
