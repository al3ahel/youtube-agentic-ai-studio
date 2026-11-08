"""Grounded subjects for the bounded, seekable vector renderer.

These are qualitative schematic actors, never generated maps, photographs,
quantities or measurements. A source sentence must support a subject before
we draw it. Unknown subjects remain an abstract labelled diagram.
"""
import re

SYMBOLS = (
    "concept", "census", "census-gap", "people", "mining", "solar",
    "battery", "circuit", "water", "plant", "network", "trade",
    "conflict", "document", "orbit", "building",
)

_RULES = (
    ("network", r"\bdata\s+mining\b"),
    ("census-gap", r"\b(?:census|population)\b.{0,100}\bunder[ -]?count|\bunder[ -]?count\w*.{0,100}\b(?:census|population)\b"),
    ("census", r"\b(?:census|censuses|enumerat\w*)\b"),
    ("mining", r"\b(?:mining|mines?|miners?|ore|copper|iron|coal|mineral\w*)\b"),
    ("solar", r"\b(?:solar|sunlight|photovoltaic\w*)\b"),
    ("battery", r"\b(?:battery|batteries|recharg\w*)\b"),
    ("circuit", r"\b(?:circuit\w*|electron\w*|electric\w*|voltage)\b"),
    ("plant", r"\b(?:plants?|roots?|forest\w*|trees?|seed\w*|photosynthesis|garden\w*)\b"),
    ("water", r"\b(?:water|river\w*|rain\w*|ocean\w*|reservoir\w*|irrigat\w*)\b"),
    ("orbit", r"\b(?:orbit\w*|satellite\w*|planet\w*|astronaut\w*|spacecraft)\b"),
    ("network", r"\b(?:network\w*|data|algorithm\w*|software|comput\w*|server\w*|internet|neural)\b"),
    ("trade", r"\b(?:trade|trading|cargo|shipping|exports?|imports?|transport\w*|supply chain)\b"),
    ("conflict", r"\b(?:conflict\w*|war|wars|fighting|border\w*|disput\w*)\b"),
    ("document", r"\b(?:records?|document\w*|treaty|treaties|law|laws|constitution|agreement\w*|history|histor\w*)\b"),
    ("building", r"\b(?:buildings?|cities|city|urban|hous\w*|school\w*|hospital\w*|factory|factories)\b"),
    ("people", r"\b(?:people|population\w*|famil\w*|communities|community|workers?|residents?|migration|migrants?)\b"),
)
_STOP = {"the", "and", "for", "with", "from", "that", "this", "into", "are", "was", "has", "its", "can", "their"}


def _words(text):
    return {word for word in re.findall(r"[^\W\d_]+", text.casefold()) if len(word) > 2 and word not in _STOP}


def _subject(text):
    return next((symbol for symbol, expression in _RULES if re.search(expression, text, re.I)), "concept")


def symbol_for_item(label, narration):
    """Prefer the item's own subject, with support in its source clause.

    We deliberately do not scan the whole film for a matching noun. Otherwise
    mentioning a mine once would make every unrelated scene show a mine.
    """
    source = re.sub(r"\b(?:without|not about|unrelated to|no mention of|excluding)\b[^.!?;]*", "", str(narration), flags=re.I)
    label = str(label)
    direct = _subject(label)
    if direct != "concept" and re.search(dict(_RULES)[direct], source, re.I):
        return direct
    tokens = _words(label)
    clauses = re.split(r"(?<=[.!?;])\s+|\n+", source)
    ranked = sorted(((len(tokens & _words(clause)), -index, clause)
                     for index, clause in enumerate(clauses)), reverse=True)
    if ranked and ranked[0][0] >= 2:
        return _subject(ranked[0][2])
    return "concept"


def apply_objects(scene, narration):
    """Opt a new/explicitly rebuilt scene into versioned object graphics."""
    scene["graphics_version"] = 1
    if scene["template"] not in {"chart", "footage"}:
        for item in scene["items"]:
            item["symbol"] = symbol_for_item(item["label"], narration)
    return scene
