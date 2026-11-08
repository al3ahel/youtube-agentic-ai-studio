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
PALETTES = {
    "ocean": {"background": "#071f33", "surface": "#12394c", "ink": "#edf7f4", "accent": "#5fd5d0", "secondary": "#e7b56b"},
    "sand": {"background": "#f2eadc", "surface": "#fffaf0", "ink": "#342c26", "accent": "#87552e", "secondary": "#326678"},
    "forest": {"background": "#112a22", "surface": "#204335", "ink": "#edf2df", "accent": "#aad08a", "secondary": "#e7be79"},
    "ember": {"background": "#261b24", "surface": "#432b36", "ink": "#fff1e7", "accent": "#f6a36b", "secondary": "#91b7ce"},
    "slate": {"background": "#182431", "surface": "#2c3c4c", "ink": "#f3f1e9", "accent": "#b7d8d5", "secondary": "#e5c27d"},
    "mono": {"background": "#f1f1eb", "surface": "#ffffff", "ink": "#1c2327", "accent": "#2e6070", "secondary": "#785d3d"},
}


def validate_brief(value):
    """A whole-film intention and a palette the renderer can actually apply."""
    fields = {"version", "theme", "premise", "continuity", "palette"}
    if (not isinstance(value, dict) or set(value) != fields or type(value.get("version")) is not int
            or value["version"] != 1 or not isinstance(value.get("palette"), str) or value["palette"] not in PALETTES):
        raise ValueError("Choose a supported versioned creative brief and film palette.")
    for field, limit in (("theme", 160), ("premise", 320), ("continuity", 320)):
        if not isinstance(value.get(field), str) or not value[field].strip() or len(value[field]) > limit:
            raise ValueError(f"The creative brief {field} needs bounded text.")
    return dict(value)


def palette_colors(brief):
    """Return a fresh named color mapping for both rendering and stock matching."""
    if isinstance(brief, str):
        if brief not in PALETTES:
            raise ValueError("Choose a supported film palette.")
        return dict(PALETTES[brief])
    return dict(PALETTES[validate_brief(brief)["palette"]])


def local_brief(script, preset="general"):
    def text(value, limit):
        return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", str(value or ""))).strip()[:limit]

    theme = text(script.get("title"), 160) or "A narrated explanation"
    spoken = " ".join(str(row.get("narration", "")) for row in script.get("sections", []) if isinstance(row, dict))
    source = theme + " " + spoken
    if preset == "news":
        palette = "mono"
    elif preset == "asmr" or re.search(r"\b(?:forest|garden|plant\w*|ecology|wildlife|nature)\b", source, re.I):
        palette = "forest"
    elif re.search(r"\b(?:flood\w*|ocean|water|river\w*|sea|rain\w*)\b", source, re.I):
        palette = "ocean"
    elif re.search(r"\b(?:histor\w*|archive\w*|census|records?|mining|ore)\b", source, re.I):
        palette = "sand"
    elif re.search(r"\b(?:space|planet\w*|orbit\w*|star\w*)\b", source, re.I):
        palette = "ember"
    else:
        digest = int(hashlib.sha256(source.encode("utf-8")).hexdigest()[:8], 16)
        palette = ("slate", "sand", "ember")[digest % 3]
    return {"version": 1, "theme": theme, "premise": text(spoken, 320) or theme,
            "continuity": "Keep the subject and palette consistent. Show each narrated change through the actors, their relationship and the camera, rather than repeating an unrelated icon.",
            "palette": palette}


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
    elif re.search(r"\b(?:flood\w*|deluge|inundat\w*|submerg\w*)\b|\bwater\b.{0,60}\b(?:cover\w*|ris\w*|engulf\w*)\b|\bris\w*\b.{0,40}\bwater\b", text, re.I):
        look, rhythm = "studio", "measured"
        reason = "Spatial scenes show the environment and human response without making every scene a technical blueprint."
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


def explicit_palette(instructions):
    text = positive_instruction_text(instructions)
    if re.search(r"\bwarm(?:er)?\s+(?:colors?|colours?|tones?|palette)\b", text):
        return "ember"
    if re.search(r"\bcool(?:er)?\s+(?:colors?|colours?|tones?|palette)\b", text):
        return "ocean"
    if not re.search(r"\bpalette\b", text):
        return None
    return next((name for name in PALETTES if re.search(r"\b" + name + r"\b", text)), None)
