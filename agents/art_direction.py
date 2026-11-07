"""Bounded creative choices shared by the director and the renderer.

The language model chooses intent, not executable HTML/CSS. A saved direction
is stable across previews, final rendering and restart. Content and channel
tone select a useful local fallback when a free model is unavailable.
"""
import hashlib
import re

LOOKS = ("editorial", "blueprint", "studio", "botanical", "poster")
RHYTHMS = ("calm", "measured", "brisk")
COMPOSITIONS = ("default", "split", "stack", "focus")
ARRANGED_TEMPLATES = frozenset({"explain", "text", "flow"})


def validate_direction(value):
    if (not isinstance(value, dict) or set(value) - {"version", "look", "rhythm", "rationale"}
            or value.get("version") != 1 or value.get("look") not in LOOKS
            or value.get("rhythm") not in RHYTHMS or not isinstance(value.get("rationale"), str)
            or not 1 <= len(value["rationale"]) <= 400):
        raise ValueError("Choose a supported visual look and motion rhythm.")
    return dict(value)


def local_direction(script, preset="general"):
    text = " ".join([str(script.get("title", "")), *[
        str(row.get("narration", "")) for row in script.get("sections", []) if isinstance(row, dict)]])
    if preset == "asmr":
        look, rhythm = "botanical", "calm"
        reason = "Soft contrast and slow entrances suit this channel's quiet pacing."
    elif preset == "news":
        look, rhythm = "editorial", "measured"
        reason = "Clear editorial hierarchy keeps dates, claims and context easy to read."
    elif re.search(r"\b(?:forest|ocean|garden|ecology|plants?|rain|wildlife|nature)\b", text, re.I):
        look, rhythm = "botanical", "measured"
        reason = "A natural palette supports the subject without adding unrelated imagery."
    elif re.search(r"\b(?:circuit|engineering|algorithm|robot|physics|solar|energy|machine)\b", text, re.I):
        look, rhythm = "blueprint", "measured"
        reason = "Technical line work supports the mechanisms described in the narration."
    else:
        # Stable variation for a new subject; rerenders never randomly restyle it.
        digest = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
        look = ("editorial", "studio", "poster")[digest % 3]
        rhythm = "brisk" if script.get("video_type") == "shorts" else "measured"
        reason = "A coherent typographic treatment with varied scene compositions suits this explanation."
    return {"version": 1, "look": look, "rhythm": rhythm, "rationale": reason}


def scene_composition(template, index, preset="general"):
    if template not in ARRANGED_TEMPLATES:
        return "default"
    choices = ("focus", "stack") if preset == "asmr" else ("split", "stack", "focus", "default")
    return choices[index % len(choices)]


def effective_composition(scene):
    """Comparison, chronology, charts and footage have their own fixed layouts."""
    return scene.get("composition", "default") if scene["template"] in ARRANGED_TEMPLATES else "default"


def positive_instruction_text(instructions):
    """Leave negated or ambiguous clauses to AI instead of inverting a request.

    This deliberately does not guess the scope of a comma-separated exclusion
    such as 'avoid blueprint, editorial or poster'. A separate positive sentence
    can still select a supported local choice.
    """
    clauses = re.split(r"[.;!?\n]|\bbut\b", instructions.casefold())
    negative = r"\b(?:no|not|never|avoid(?:ing)?|without|except|remove|drop|exclude|stop|instead\s+of|rather\s+than|away\s+from|[a-z]+n['’]t|dont)\b"
    return " ".join(clause for clause in clauses if not re.search(negative, clause))


def explicit_direction(instructions):
    """Only recognize choices whose visual effect the renderer can guarantee."""
    text = positive_instruction_text(instructions)
    look = next((name for name in LOOKS if re.search(r"\b" + name + r"\b", text)), None)
    rhythm = next((name for name in RHYTHMS if re.search(r"\b" + name + r"\b", text)), None)
    composition = next((name for name in ("split", "stack", "focus")
                        if re.search(r"\b" + name + r"(?:ed)?\b", text)), None)
    return look, rhythm, composition
