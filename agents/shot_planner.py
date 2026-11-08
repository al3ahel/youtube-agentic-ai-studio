"""Narration-owned shot planning and a bounded vocabulary for actual compositions.

A shot is a normal scene with optional direction; old saved scenes keep their
original renderer. New cuts use measured word/phrase boundaries where available.
Neither a model nor this module can change speech, manufacture media, or claim
that a schematic is a physical simulation.
"""
from __future__ import annotations

import copy
import math
import re

ROLES = ("establish", "detail", "interaction", "payoff")
FRAMINGS = ("wide", "medium", "close", "macro")
TECHNIQUES = ("stage2d", "collage", "footage", "diagram3d")
CAMERAS = ("locked", "push", "orbit", "track")
TRANSITIONS = ("cut", "match", "dissolve")
MATERIALS = ("steel", "mail", "ceramic", "fiber", "mixed")
MECHANISMS = ("armour", "layers")
MECHANISM_ACTIONS = ("reveal", "approach", "impact", "deflect", "penetrate", "separate", "absorb", "compare")


def _line(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError("Shot direction needs bounded plain text.")
    if re.search(r"<[^>]*>|://", value):
        raise ValueError("Shot direction cannot contain markup or links.")
    return value


def validate_shot(value):
    fields = {"version", "role", "framing", "technique", "camera", "transition", "focus", "action"}
    if not isinstance(value, dict) or set(value) != fields or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("A shot needs the supported version 1 direction fields.")
    for key, choices in (("role", ROLES), ("framing", FRAMINGS), ("technique", TECHNIQUES), ("camera", CAMERAS), ("transition", TRANSITIONS)):
        if not isinstance(value[key], str) or value[key] not in choices:
            raise ValueError(f"Unsupported shot {key}.")
    _line(value["focus"], 100)
    _line(value["action"], 160)
    return copy.deepcopy(value)


def validate_mechanism(value):
    fields = {"version", "kind", "material", "action"}
    if not isinstance(value, dict) or set(value) != fields or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("A mechanism needs the supported version 1 fields.")
    for key, choices in (("kind", MECHANISMS), ("material", MATERIALS), ("action", MECHANISM_ACTIONS)):
        if not isinstance(value[key], str) or value[key] not in choices:
            raise ValueError(f"Unsupported mechanism {key}.")
    return copy.deepcopy(value)


def shot_schema():
    fields = {key: {"type": "string", "enum": list(values)} for key, values in
              (("role", ROLES), ("framing", FRAMINGS), ("technique", TECHNIQUES), ("camera", CAMERAS), ("transition", TRANSITIONS))}
    fields.update(version={"type": "integer", "enum": [1]}, focus={"type": "string", "minLength": 1, "maxLength": 100},
                  action={"type": "string", "minLength": 1, "maxLength": 160})
    return {"type": "object", "additionalProperties": False, "required": list(fields), "properties": fields}


def mechanism_schema():
    fields = {"version": {"type": "integer", "enum": [1]},
              "kind": {"type": "string", "enum": list(MECHANISMS)},
              "material": {"type": "string", "enum": list(MATERIALS)},
              "action": {"type": "string", "enum": list(MECHANISM_ACTIONS)}}
    return {"type": "object", "additionalProperties": False, "required": list(fields), "properties": fields}


def shot_counts(sections, *, calm=False, maximum=120):
    # Short hooks can hold once; an explanation gets enough room for a detail
    # and interaction. Calm channels intentionally keep longer shots.
    target = 7.0 if calm else 4.2
    counts = [max(1, min(12, math.ceil(float(s["duration"]) / target))) for s in sections]
    while sum(counts) > maximum:
        choices = [i for i, count in enumerate(counts) if count > 1]
        if not choices:
            break
        index = min(choices, key=lambda i: float(sections[i]["duration"]) / counts[i])
        counts[index] -= 1
    return counts


def cut_boundaries(section, count, plan):
    """Align cuts to real speech gaps without moving the measured section ends."""
    start, length, fps = float(section["start"]), float(section["duration"]), int(plan["fps"])
    end = start + length
    count = max(1, min(count, max(1, int(length * fps))))
    cuts = [start]
    candidates = []
    for cue in plan.get("captions", []):
        if start < cue["end"] < end:
            candidates.append((float(cue["end"]), 0.2 if re.search(r"[.!?;؟]$", cue["text"].strip()) else 0.05))
    words = [w for w in plan.get("words", []) if start <= w["start"] < end]
    for i, word in enumerate(words):
        if word["end"] >= end:
            continue
        gap = max(0, words[i + 1]["start"] - word["end"]) if i + 1 < len(words) else 0
        if gap > .06 or re.search(r"[.!?;؟]$", word["word"]):
            candidates.append((float(word["end"]) + min(gap / 2, .15), .35 + min(gap, .3)))
    minimum = min(1.4, length / count * .55)
    for index in range(1, count):
        target = start + length * index / count
        low = cuts[-1] + minimum
        high = end - (count - index) * minimum
        nearby = [(t, weight) for t, weight in candidates if low <= t <= high and abs(t - target) <= min(1.4, length / count * .36)]
        chosen = min(nearby, key=lambda pair: abs(pair[0] - target) - pair[1])[0] if nearby else target
        chosen = max(low, min(high, round(chosen * fps) / fps))
        cuts.append(round(chosen, 6))
    cuts.append(round(end, 6))
    return cuts


_ARMOUR = r"\b(?:armou?r|breastplates?|chain\s?mail|steel\s+plates?|mail(?=\s+(?:uses?|has|consists?\s+of|is\s+made\s+of)\s+(?:linked\s+|interlocking\s+)?rings))\b"
_LAYER = r"\b(?:layers?|laminat\w*|composite|ceramic|fib(?:er|re)|kevlar|woven)\b"
_PROTECTIVE = r"\b(?:armou?r|breastplate|projectile|bullet|protect\w*|impact|deflect\w*|penetrat\w*|ballistic|reinforc\w*)\b"
_PROJECTILE = r"\b(?:bullets?|projectiles?|ballistic|firearms?|gunshots?|gunfire|pistols?|muskets?|proof\s+tests?)\b"
_MATERIAL_PATTERNS = (
    ("mail", r"\b(?:chain\s?mail|mail|(?:linked|interlocking)\s+rings)\b"),
    ("ceramic", r"\bceramic\w*\b"),
    ("fiber", r"\b(?:fib(?:er|re)\w*|kevlar|woven|cloth)\b"),
    ("steel", r"\b(?:steel|iron|breastplates?|plate\s+armou?r)\b"),
)
_ACTION = {
    "approach": r"\b(?:approach\w*|towards?|arriv\w*|incoming|flying)\b",
    "impact": r"\b(?:impact\w*|strik\w*|hit\w*|collision|collid\w*|contact)\b",
    "deflect": r"\b(?:deflect\w*|bounc\w*|ricochet\w*|glanc\w*)\b",
    "penetrate": r"\b(?:penetrat\w*|pierc\w*|passes?\s+through|punch\w*\s+through)\b",
    "separate": r"\b(?:layers?|separat\w*|cutaway|cross\s?section|inside)\b",
    "absorb": r"\b(?:absorb\w*|dissipat\w*|deform\w*|stop\w*|(?:spread\w*|distribut\w*)\s+(?:the\s+)?(?:force|energy|impact)|slow\w*\s+(?:the\s+)?(?:bullet|projectile|impact))\b",
    "compare": r"\b(?:depend\w*|compar\w*|versus|unlike|thickness|thicker|thinner|range|var\w*|different)\b",
}


def _positive_match(expression, text):
    for match in re.finditer(expression, text, re.I):
        before = text[max(0, match.start() - 45):match.start()]
        if not re.search(r"\b(?:not|never|cannot|can't|couldn't|doesn't|don't|didn't|isn't|wasn't|won't|wouldn't|no)\b[^.!?;]{0,25}$", before, re.I):
            return True
    return False


def _supported_action(action, text):
    if action == "compare":
        # The contract does not yet name which physical property to vary.
        # A thickness/range comparison must not become arbitrary geometry.
        return False
    # A question about whether armour stops a projectile does not establish
    # that it did. Likewise, "firearms spread" is not force distribution.
    if action in {"deflect", "penetrate", "absorb", "impact"}:
        return any(_positive_match(_ACTION[action], sentence)
                   for sentence in re.findall(r"[^.!?]+[.!?]?", text)
                   if not sentence.rstrip().endswith("?"))
    return _positive_match(_ACTION[action], text)


def _materials(text):
    materials = [name for name, expression in _MATERIAL_PATTERNS if _positive_match(expression, text)]
    # Steel describes what these links are made from, not an additional plate.
    if set(materials) == {"mail", "steel"} and re.search(r"\b(?:steel|iron)\s+(?:chain\s?)?mail\b", text, re.I):
        materials.remove("steel")
    return materials


def _mechanism_source(narration, context):
    materials = _materials(narration)
    if len(materials) <= 1:
        if not materials and len(_materials(context)) > 1:
            return None
        return narration
    # Do not borrow an outcome from another material's sentence. A short shot
    # may contain both a setup and its payoff; use a concrete action-bearing
    # clause when it unambiguously names one material, otherwise abstain.
    candidates = [part.strip() for part in re.findall(r"[^.!?;]+[.!?;]?", narration)
                  if len(_materials(part)) == 1]
    if not candidates:
        return None
    return next((part for part in candidates if any(_supported_action(action, part)
                for action in ("deflect", "penetrate", "absorb", "impact", "separate", "compare"))), candidates[0])


def _projectile_supported(narration, context=""):
    if re.search(r"\b(?:absorb\w*|deflect\w*|stop\w*)\s+(?:the\s+)?(?:heat|moisture|water|light|sound)\b", narration, re.I):
        return False
    return _positive_match(_PROJECTILE, narration) or _positive_match(_PROJECTILE, context)


def mechanism_for(narration, context=""):
    narration = _mechanism_source(narration, context)
    if narration is None:
        return None
    # Section context may resolve a pronoun, but cannot turn an unrelated shot
    # about a different subject into the previous material diagram.
    refers_back = bool(re.search(r"^(?:its?\b|this\b|these\b|that\b|the\s+(?:material|plate|surface|outcome|result|thickness)\b|(?:a|the)\s+(?:surviving\s+)?dent\b)", narration.strip(), re.I))
    current_armour = _positive_match(_ARMOUR, narration)
    contextual_armour = refers_back and _positive_match(_ARMOUR, context)
    protective = _positive_match(_PROTECTIVE, narration) or refers_back and _positive_match(_PROTECTIVE, context)
    layers = _positive_match(_LAYER, narration) and protective
    kind = "layers" if layers else "armour" if current_armour or contextual_armour else None
    if kind is None:
        return None
    material = next(iter(_materials(narration)), None)
    if material is None:
        if re.search(r"\b(?:bronze|copper|leather|wooden|titanium|alumin(?:ium|um))\b", narration, re.I):
            return None
        material = next(iter(_materials(context)), None)
    if material is None:
        # Unknown historic materials are not silently illustrated as steel.
        return None
    if material == "mail":
        kind = "armour"  # Layered mail still consists of rings, never solid sheets.
    action = next((name for name in ("compare", "deflect", "penetrate", "absorb", "separate", "impact", "approach")
                   if _supported_action(name, narration)
                   and (name not in {"approach", "impact", "deflect", "penetrate", "absorb"}
                        or _projectile_supported(narration, context))), "reveal")
    return {"version": 1, "kind": kind, "material": material, "action": action}


def grounding_issues(mechanism, narration, context=""):
    validate_mechanism(mechanism)
    supported = mechanism_for(narration, context)
    if supported is None:
        return ["The shot's source does not support this material mechanism."]
    issues = []
    if mechanism["kind"] != supported["kind"] or mechanism["material"] != supported["material"]:
        issues.append("The shot changed the source material or subject.")
    evidence = _mechanism_source(narration, context) or ""
    if mechanism["action"] in {"approach", "impact", "deflect", "penetrate", "absorb"} and not _projectile_supported(evidence, context):
        issues.append("The contact animation requires a source-supported projectile.")
    if mechanism["action"] != "reveal" and not _supported_action(mechanism["action"], evidence):
        issues.append("The depicted outcome is not supported by this shot's narration.")
    return issues


def direct_shot(scene, narration, section, index, count, *, global_index=0, final=False, calm=False):
    if final:
        role = "payoff"
    elif global_index == 0:
        role = "establish"
    elif count > 1 and index == count - 1:
        role = "interaction"
    elif index == 0 and global_index > 0:
        role = "detail"
    else:
        role = ("interaction", "detail")[index % 2]
    mechanism = mechanism_for(narration, str(section.get("narration", ""))) if scene.get("template") != "chart" else None
    framing = {"establish": "wide", "detail": "macro", "interaction": "medium", "payoff": "wide"}[role]
    technique = "stage2d"
    if mechanism and role in {"detail", "interaction"}:
        # Depth serves an explanatory mechanism, not a mandatory visual style.
        technique = "diagram3d" if mechanism["action"] in {"separate", "absorb", "compare", "impact", "deflect", "penetrate"} else "stage2d"
    focus = " ".join(str(scene.get("title", "Subject")).split()[:12])[:100] or "Subject"
    action = " ".join(narration.split()[:24])[:160].rstrip() or "Reveal the narrated subject."
    shot = {"version": 1, "role": role, "framing": framing, "technique": technique,
            "camera": "locked" if calm else "orbit" if technique == "diagram3d" and role == "detail" else
                      "track" if role == "interaction" else "push" if role == "detail" else "locked",
            "transition": "dissolve" if calm else "match" if index > 0 else "cut", "focus": focus, "action": action}
    scene["shot"] = validate_shot(shot)
    if mechanism:
        scene["mechanism"] = mechanism
    return scene


def reconcile_media(scene):
    """Honor trusted acquired media after AI direction, keeping unavailable modes honest."""
    shot = scene.get("shot")
    if not shot:
        return
    if scene.get("template") == "chart":
        scene.pop("mechanism", None)
        scene.pop("media_layers", None)
        shot["technique"] = "stage2d"
    elif scene.get("footage"):
        shot["technique"] = "footage"
        scene.pop("media_layers", None)
        scene.pop("mechanism", None)
    elif scene.get("image"):
        shot["technique"] = "collage" if len(scene.get("media_layers", [])) >= 2 else "stage2d"
        scene.pop("mechanism", None)
    else:
        scene.pop("media_layers", None)
        if shot["technique"] in {"collage", "footage"}:
            shot["technique"] = "stage2d"
        if shot["technique"] == "diagram3d" and not scene.get("mechanism"):
            shot["technique"] = "stage2d"
    if shot["technique"] != "diagram3d" and shot["camera"] == "orbit":
        shot["camera"] = "push"


def validate_scene(scene):
    if "mechanism_result_view" in scene:
        raise ValueError("A settled result view is derived from the source during render preparation.")
    if "mechanism" in scene:
        validate_mechanism(scene["mechanism"])
        if "shot" not in scene:
            raise ValueError("A material mechanism needs shot direction.")
    if "shot" not in scene:
        if "media_layers" in scene:
            raise ValueError("Collage layers need shot direction.")
        return
    shot = validate_shot(scene["shot"])
    if scene.get("template") == "chart" and (scene.get("mechanism") or shot["technique"] != "stage2d"):
        raise ValueError("A chart uses its supplied-data renderer, not a material mechanism.")
    if shot["technique"] == "diagram3d" and not scene.get("mechanism"):
        raise ValueError("A 3D explanation needs a supported material mechanism.")
    if shot["technique"] == "footage" and not scene.get("footage"):
        raise ValueError("A footage shot needs an acquired clip.")
    if shot["technique"] == "collage" and (not scene.get("image") or not isinstance(scene.get("media_layers"), list) or len(scene["media_layers"]) < 2):
        raise ValueError("A collage shot needs two or more acquired pictures.")
    if "media_layers" in scene:
        layers = scene["media_layers"]
        if (shot["technique"] != "collage" or not isinstance(layers, list) or not 2 <= len(layers) <= 4
                or any(not isinstance(layer, dict) or not isinstance(layer.get("path"), str) for layer in layers)):
            raise ValueError("A collage needs two to four actual local pictures.")
        if len({layer["path"] for layer in layers}) != len(layers):
            raise ValueError("Copies of the same picture are not distinct collage layers.")


def contact_beat(scene, words, narration=None, context=""):
    """Use a real spoken action as the shared visual/sound contact anchor."""
    action = scene.get("mechanism", {}).get("action")
    if action not in {"approach", "impact", "deflect", "penetrate", "absorb"}:
        return None
    events = [word for word in words if scene["start"] <= word["start"] < scene["start"] + scene["duration"]]
    source = narration if narration is not None else " ".join(word["word"] for word in events)
    evidence = _mechanism_source(source, context)
    if not evidence:
        return None
    normalize = lambda value: " ".join(re.findall(r"\w+", value.casefold()))
    # Prefer a stated physical contact to a later description of its outcome.
    # An ambiguous repeated word stays unanchored; no guessed word time is saved.
    for cue_action in (("impact", action) if action in {"absorb", "deflect", "penetrate"} else (action,)):
        for sentence in re.findall(r"[^.!?;]+[.!?;]?", evidence):
            if sentence.rstrip().endswith("?"):
                continue
            for match in re.finditer(_ACTION[cue_action], sentence, re.I):
                if not _positive_match(_ACTION[cue_action], sentence[:match.end()]):
                    continue
                prefix = sentence[max(0, match.start() - 45):match.start()]
                if not _positive_match(_ACTION[cue_action], prefix + match.group(0)):
                    continue
                token = normalize(match.group(0).split()[0])
                matches = [word for word in events if normalize(word["word"]) == token]
                if len(matches) == 1:
                    return {"time": round(matches[0]["start"] - scene["start"], 6), "target": "scene", "action": "highlight"}
    return None


def mechanism_result_view(previous, scene, previous_narration, narration, context="", fps=30):
    """Recognize an explicit result of the adjacent, same-material interaction.

    This is a source-derived viewing mode, not a new model instruction or a
    different outcome. Uncertain references and another contact keep the normal
    action sequence. Callers supply shot text recovered from measured speech.
    """
    contact_actions = {"impact", "deflect", "penetrate", "absorb"}
    for shot in (previous, scene):
        if (not isinstance(shot, dict) or shot.get("template") == "chart"
                or shot.get("image") or shot.get("footage") or shot.get("media_layers")
                or shot.get("shot", {}).get("technique") not in {"stage2d", "diagram3d"}
                or not isinstance(shot.get("mechanism"), dict)
                or shot["mechanism"].get("action") not in contact_actions):
            return False
    if (not scene.get("section_id") or previous.get("section_id") != scene["section_id"]
            or abs(previous["start"] + previous["duration"] - scene["start"]) > 1 / fps + .00001):
        return False
    before, after = previous["mechanism"], scene["mechanism"]
    if any(before.get(key) != after.get(key) for key in ("kind", "material")):
        return False
    if before["action"] != "impact" and before["action"] != after["action"]:
        return False
    if not previous_narration.strip() or not narration.strip():
        return False
    combined = (previous_narration + " " + narration).replace("’", "'")
    if re.search(r"[?؟]|\b\w+n't\b", combined, re.I) or re.search(
            r"\b(?:not|never|cannot|can't|couldn't|doesn't|don't|didn't|isn't|wasn't|won't|wouldn't|no|"
            r"if|whether|may|might|could|would|perhaps|possibly|maybe|seem\w*|suggest\w*|hypothetic\w*|"
            r"another|again|second|third|next|new|different|additional|repeat\w*|twice|or)\b", combined, re.I):
        return False
    if re.search(r"\b(?:two|three|four|several|multiple|[2-9]|[1-9]\d+)\s+(?:bullets?|projectiles?|shots?|hits?|impacts?)\b", combined, re.I):
        return False
    result_noun = re.search(
        r"^(?:a|the|this|that)\s+(?:(?:surviving|resulting|remaining|visible)\s+)?"
        r"(?:dent|hole|deformation|damage|mark|result|outcome)\b", narration.strip(), re.I)
    retrospective = (re.search(r"^(?:it|this|that|its|the\s+(?:plate|surface|material))\b", narration.strip(), re.I)
                     and re.search(r"\b(?:had\s+(?:been\s+)?(?:stopp\w*|absorb\w*|deflect\w*|penetrat\w*)|remain\w*)\b", narration, re.I))
    if not (result_noun or retrospective):
        return False
    if not re.search(r"\b(?:dents?|holes?|deform\w*|bullets?|projectiles?|shots?|plates?|breastplates?|armou?r|rings|fib(?:er|re)s?|ceramic)\b", narration, re.I):
        return False
    # Even a new hit phrased with an anaphor must not become a frozen result.
    if re.search(_ACTION["impact"] + "|" + _ACTION["approach"], narration, re.I):
        return False
    if any(sum(_supported_action(action, text) for action in ("deflect", "penetrate", "absorb")) > 1
           for text in (previous_narration, narration)):
        return False
    prior_contacts = list(re.finditer(_ACTION["impact"], previous_narration, re.I))
    if len(prior_contacts) > 1 or (not prior_contacts and len(list(re.finditer(
            _ACTION[before["action"]], previous_narration, re.I))) != 1):
        return False
    try:
        return not (grounding_issues(before, previous_narration, context)
                    or grounding_issues(after, narration, context))
    except (ValueError, KeyError, TypeError):
        return False


def result_view_scene_ids(plan):
    """Derive settled-view IDs from exact measured words and original narration.

    Caption chunks may overlap a cut and cannot establish a new/result event.
    A complete token match recovers source punctuation (including questions)
    without trusting a title, model action description or a supplied flag.
    """
    if plan.get("timing_source") != "measured_sections":
        return set()
    scenes = plan.get("scenes", [])
    sections = {section["id"]: section for section in plan.get("sections", [])}
    words = plan.get("words", [])
    if len(scenes) > 256 or len(words) > 30000:
        return set()
    tokens = re.compile(r"\w+(?:['’]\w+)*")
    normalize = lambda value: value.casefold().replace("’", "'")
    sources = {}
    for section_id, section in sections.items():
        if section.get("caption_timing") != "word":
            continue
        narration = section.get("narration", "")
        source_words = list(tokens.finditer(narration))
        measured = sorted((word for word in words if str(word.get("section_id", "")) == section_id
                           and section["start"] <= word["start"] < section["start"] + section["duration"]
                           and word["start"] < word["end"] <= section["start"] + section["duration"] + 1 / plan.get("fps", 30) + .00001),
                          key=lambda word: word["start"])
        measured_tokens = [tokens.findall(word["word"]) for word in measured]
        if (not source_words or len(source_words) != len(measured)
                or any(len(row) != 1 for row in measured_tokens)
                or [normalize(row[0]) for row in measured_tokens] != [normalize(row.group(0)) for row in source_words]):
            continue
        for scene in scenes:
            if scene.get("section_id") != section_id:
                continue
            indices = [index for index, word in enumerate(measured)
                       if scene["start"] <= word["start"] < scene["start"] + scene["duration"]]
            if not indices:
                continue
            first, last = indices[0], indices[-1]
            if indices != list(range(first, last + 1)):
                continue
            end = source_words[last + 1].start() if last + 1 < len(source_words) else len(narration)
            sources[scene["id"]] = narration[source_words[first].start():end].strip()
    result = set()
    for previous, scene in zip(scenes, scenes[1:]):
        if (previous["id"] in result or previous["id"] not in sources or scene["id"] not in sources):
            continue
        section = sections.get(scene.get("section_id"))
        if section and mechanism_result_view(previous, scene, sources[previous["id"]], sources[scene["id"]],
                                             section["narration"], plan.get("fps", 30)):
            result.add(scene["id"])
    return result


def report(plan):
    scenes = plan["scenes"]
    rows = [{"scene_id": scene["id"], "section_id": scene["section_id"], "start": scene["start"],
             "duration": scene["duration"], **scene["shot"]} for scene in scenes if scene.get("shot")]
    return {"version": 1, "source": "narration_boundaries_and_constrained_direction", "shots": rows,
            "summary": f"{len(rows)} shots across {len(plan['sections'])} narration sections; cuts preserve the recording.",
            "qualification": "Shot planning is not rendered visual approval. Material animations are qualitative schematics, not ballistic predictions."}
