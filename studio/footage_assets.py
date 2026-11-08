"""Keep film footage and its credits bound to the current storyboard.

The plan is the source of truth. A download ledger may include candidates that
were rejected or scenes that were later replaced; those are not used credits.
"""
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import urlsplit


def credits_for_plan(plan):
    """Return only referenced video credits, including legacy Pexels scenes."""
    if not isinstance(plan, dict) or not isinstance(plan.get("scenes"), list):
        return []
    result, seen = [], set()
    for scene in plan["scenes"]:
        footage = scene.get("footage") if isinstance(scene, dict) else None
        if not isinstance(footage, dict):
            continue
        source = footage.get("source_url", "")
        try:
            parsed = urlsplit(source)
            if parsed.scheme != "https" or parsed.username or parsed.password:
                continue
            host = parsed.hostname
        except (TypeError, ValueError):
            continue
        provider = {"www.pexels.com": "pexels", "pexels.com": "pexels",
                    "pixabay.com": "pixabay", "www.pixabay.com": "pixabay"}.get(host)
        if not provider:
            continue
        receipt = footage.get("selection") or {}
        if not isinstance(receipt, dict):
            continue
        key = (source, footage.get("path"))
        if key in seen:
            continue
        seen.add(key)
        label = "Pixabay" if provider == "pixabay" else "Pexels"
        credit = footage.get("credit") or f"Video from {label}"
        item = {"provider": provider, "source": f"{label} video", "source_url": source,
                "link": source, "credit": credit, "path": footage.get("path", "")}
        for field in ("author", "license", "license_url", "provider_id"):
            if receipt.get(field):
                item[field] = deepcopy(receipt[field])
        result.append(item)
    return result


def current_video_credits(output):
    """Prefer committed scene references; a malformed plan never revives a ledger."""
    output = Path(output)
    path = output / "scene_plan.json"
    if path.exists() or path.is_symlink():
        try:
            if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
                return []
            return credits_for_plan(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return []
    try:
        legacy = output / "video_credits.json"
        if legacy.is_symlink() or legacy.stat().st_size > 2 * 1024 * 1024:
            return []
        records = json.loads(legacy.read_text(encoding="utf-8"))
        return records if isinstance(records, list) else []
    except (OSError, ValueError, TypeError):
        return []


def validate_bound_footage(plan, output, *, script_signature, profile_id):
    """Reject changed or wrong-project modern assets; preserve explicit old plans.

Legacy scenes have no acquisition receipt. Their existing local-file and render
validation still applies. We never generate a receipt to bless an old file.
"""
    from apis.stock_video import verify_footage
    for scene in plan.get("scenes", []) if isinstance(plan, dict) else []:
        footage = scene.get("footage")
        if not isinstance(footage, dict):
            continue
        if verify_footage(footage, output, script_signature=script_signature,
                          profile_id=profile_id, scene_id=str(scene.get("id", ""))) is False:
            raise ValueError("The saved footage changed or belongs to another script, channel or scene. "
                             "Rebuild Visuals to select footage for this project before rendering.")
