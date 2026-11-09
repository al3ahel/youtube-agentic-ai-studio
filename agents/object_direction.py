"""Grounded subjects for the bounded, seekable vector renderer.

These are qualitative schematic actors, never generated maps, photographs,
quantities or measurements. A source sentence must support a subject before
we draw it. Unknown subjects remain an abstract labelled diagram.
"""
import re
from copy import deepcopy

SYMBOLS = (
    "concept", "census", "census-gap", "people", "mining", "solar",
    "battery", "circuit", "water", "plant", "network", "trade",
    "conflict", "document", "orbit", "building", "armour", "firearm",
)
STAGING_ACTIONS = {
    "flood": ("rise", "adapt", "float"),
    "census": ("record", "gap"),
    "mining": ("extract", "transfer"),
    "generic": ("reveal", "assemble", "connect", "compare"),
}
CAMERAS = ("locked", "wide", "detail", "follow", "reveal")
ROLES = ("subject", "support", "context")

_RULES = (
    ("network", r"\bdata\s+mining\b"),
    ("census-gap", r"\b(?:census|population)\b.{0,100}\bunder[ -]?count|\bunder[ -]?count\w*.{0,100}\b(?:census|population)\b"),
    ("census", r"\b(?:census|censuses|enumerat\w*)\b"),
    ("armour", r"\b(?:armou?r|breastplates?|cuirass(?:es)?)\b"),
    ("firearm", r"\b(?:firearms?|muskets?|pistols?|handguns?|arquebus(?:es)?|rifles?(?!\s+through\b))\b"),
    ("mining", r"\b(?:mining|mines?|miners?|ore|copper|iron|coal|mineral\w*)\b"),
    ("solar", r"\b(?:solar|sunlight|photovoltaic\w*)\b"),
    ("battery", r"\b(?:battery|batteries|recharg\w*)\b"),
    ("circuit", r"\b(?:circuit\w*|electron\w*|electric\w*|voltage)\b"),
    ("plant", r"\b(?:plants?|roots?|forest\w*|trees?|seed\w*|photosynthesis|garden\w*)\b"),
    ("water", r"\b(?:water|river\w*|rain\w*|ocean\w*|reservoir\w*|irrigat\w*|flood\w*)\b"),
    ("orbit", r"\b(?:orbit\w*|satellite\w*|planet\w*|astronaut\w*|spacecraft)\b"),
    ("network", r"\b(?:network\w*|data|algorithm\w*|software|comput\w*|server\w*|internet|neural)\b"),
    ("trade", r"\b(?:trade|trading|cargo|shipping|exports?|imports?|transport\w*|supply chain)\b"),
    ("conflict", r"\b(?:conflict\w*|war|wars|fighting|border\w*|disput\w*)\b"),
    ("document", r"\b(?:records?|document\w*|treaty|treaties|law|laws|constitution|agreement\w*|history|histor\w*)\b"),
    ("building", r"\b(?:buildings?|cities|city|urban|hous\w*|homes?|shelters?|settlements?|school\w*|hospital\w*|factory|factories)\b"),
    ("people", r"\b(?:people|human\w*|population\w*|famil\w*|communities|community|workers?|residents?|migration|migrants?|survivors?|engineers?)\b"),
)
# These are whole object-name labels, narrower than the contextual evidence
# above: "history" can suggest records, but is still a legitimate abstraction.
# A model must not replace a named, supported catalogue object with a cube.
_LITERAL_LABELS = {
    "census": r"census|censuses",
    "people": r"people|humans?|famil(?:y|ies)|communities|community|(?:rescue |construction |census )?workers?|residents?|migrants?|survivors?|engineers?",
    "mining": r"mining|mines?|miners?|ore|copper|iron|coal|minerals?",
    "solar": r"solar(?: panels?)?|sunlight|photovoltaic panels?",
    "battery": r"battery|batteries",
    "circuit": r"(?:electric(?:al)? )?circuits?|electrons?",
    "water": r"water|rivers?|rain|oceans?|reservoirs?|floodwater",
    "plant": r"plants?|roots?|forests?|trees?|seeds?|gardens?",
    "network": r"(?:(?:computer|data|neural) )?networks?|computers?|servers?|internet",
    "trade": r"cargo",
    "document": r"records?|documents?|treaty|treaties|constitutions?|agreements?",
    "orbit": r"satellites?|planets?|astronauts?|spacecraft",
    "building": r"(?:(?:floating|raised|flooded|school) )?(?:buildings?|cities|city|houses?|homes?|shelters?|settlements?|schools?|hospitals?|factory|factories)",
    "armour": r"(?:body )?armou?r|breastplates?|cuirass(?:es)?",
    "firearm": r"firearms?|muskets?|pistols?|handguns?|arquebus(?:es)?|rifles?",
}
_STOP = {"the", "and", "for", "with", "from", "that", "this", "into", "are", "was", "has", "its", "can", "their"}
_WORLD_EVIDENCE = {
    "flood": r"\b(?:flood\w*|deluge|inundat\w*|submerg\w*)\b|\bwater\b.{0,60}\b(?:cover\w*|ris\w*|engulf\w*)\b|\bris\w*\b.{0,40}\bwater\b|\b(?:rising|covering)\s+(?:sea|water)\b|\bsea.level\b.{0,30}\bris\w*\b",
    "census": r"\b(?:census|censuses|enumerat\w*|population\s+count\w*)\b",
    "mining": r"\b(?:mining|mines?|miners?|ore|mineral\w*)\b",
}
_ACTION_EVIDENCE = {
    "float": r"\b(?:float\w*|buoyan\w*)\b",
    "adapt": r"\b(?:adapt\w*|surviv\w*|redesign\w*|float\w*|builds?|built|building(?=\s+(?:a|an|the|new)\b)|construct\w*|engineer\w*|resilien\w*|protect\w*|sheltering)\b",
    "gap": r"\b(?:under[ -]?count\w*|missing|omitt\w*|unrecord\w*|uncount\w*|exclude\w*|not\s+count\w*)\b",
    "transfer": r"\b(?:mov\w*|transfer\w*|conveyor\w*|transport\w*|carry\w*|carried|carries|load\w*)\b",
    "assemble": r"\b(?:builds?|built|building(?=\s+(?:a|an|the|new)\b)|design\w*|assembl\w*|construct\w*|develop\w*|creat\w*|combin\w*|form\w*|make\w*|made)\b",
    "connect": r"\b(?:connect\w*|link\w*|network\w*|transfer\w*|flow\w*|power\w*|suppl\w*|convert\w*|relat\w*|communicat\w*|process\w*|distribut\w*|carries|carry\w*|carried|feeds?|fed)\b",
    "compare": r"\b(?:compar\w*|versus|vs|whereas|unlike|differ\w*|alternativ\w*|instead)\b",
}


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
    if direct != "concept" and symbol_supported(direct, source):
        return direct
    tokens = _words(label)
    clauses = re.split(r"(?<=[.!?;])\s+|\n+", source)
    ranked = sorted(((len(tokens & _words(clause)), -index, clause)
                     for index, clause in enumerate(clauses)), reverse=True)
    if ranked and ranked[0][0] >= 2:
        subject = _subject(ranked[0][2])
        return subject if symbol_supported(subject, ranked[0][2]) else "concept"
    return "concept"


def apply_objects(scene, narration):
    """Opt a new/explicitly rebuilt scene into versioned object graphics."""
    scene["graphics_version"] = 1
    if scene["template"] not in {"chart", "footage"}:
        for item in scene["items"]:
            item["symbol"] = symbol_for_item(item["label"], narration)
    return scene


def validate_staging(value):
    """The renderer owns geometry; the plan owns these bounded directing choices."""
    if (not isinstance(value, dict) or set(value) != {"version", "world", "action", "camera"}
            or type(value.get("version")) is not int or value["version"] != 1
            or not isinstance(value.get("world"), str) or value["world"] not in STAGING_ACTIONS
            or value.get("action") not in STAGING_ACTIONS[value["world"]]
            or value.get("camera") not in CAMERAS):
        raise ValueError("Choose a supported scene setting, action and camera.")
    return dict(value)


def _source_text(narration):
    # A direct exclusion must not become evidence for the excluded actor.
    return re.sub(r"\b(?:without|not about|unrelated to|no mention of|excluding)\b[^.!?;]*", "", str(narration), flags=re.I)


def paired_subjects(narration, context=""):
    """Recover two explicitly related names without importing another action.

    A measured cut can split ``Armour and | firearms evolved together``.
    Only a unique lexical match to the original enclosing clause may supply
    the missing name, and the relationship cue must belong to this shot. The
    returned labels are identity evidence; surrounding verbs are never action
    evidence. Ambiguous excerpts and pairs in another subject's clause abstain.
    """
    tokens = re.compile(r"\w+(?:['’]\w+)*")
    normalise = lambda value: value.casefold().replace("’", "'")
    owned = [normalise(value) for value in tokens.findall(str(narration))]
    original = list(tokens.finditer(str(context)))
    source_tokens = [normalise(match.group()) for match in original]
    source = str(narration)
    begin, end = 0, len(source)
    if owned and source_tokens:
        positions = [index for index in range(len(source_tokens) - len(owned) + 1)
                     if source_tokens[index:index + len(owned)] == owned]
        if len(positions) != 1:
            return []
        index = positions[0]
        source = str(context)
        begin, end = original[index].start(), original[index + len(owned) - 1].end()
    if not owned:
        return []
    relationship = re.compile(r"\b(?:depend\w*\s+on|together|both|each\s+other|compar\w*|versus)\b", re.I)
    # These cues cannot reach through a second clause merely because its
    # sentence happens to contain two catalogue nouns. Keep source offsets so
    # the accepted relationship still belongs to the measured shot excerpt.
    boundaries = list(re.finditer(r"[.!?؟;]+|\b(?:while|whereas)\b", source, re.I))
    spans = []
    left = 0
    for boundary in boundaries:
        spans.append((left, boundary.start()))
        left = boundary.end()
    spans.append((left, len(source)))

    # A small subject-preserving predicate can connect a coordinated pair to
    # its cue (including "were connected, then evolved together"). Unknown
    # intervening nouns, new subjects and conjunctions abstain; this is not a
    # general grammar parser and must not infer who an arbitrary verb concerns.
    predicate_words = re.compile(
        r"(?:is|are|was|were|be|been|being|has|have|had|can|could|will|would|may|might|must|should|"
        r"also|still|then|later|often|always|sometimes|gradually|slowly|rapidly|already|now|with|alongside|"
        r"evolv(?:e|es|ed|ing)|develop(?:s|ed|ing)?|work(?:s|ed|ing)?|chang(?:e|es|ed|ing)|"
        r"grow|grows|grew|grown|interact(?:s|ed|ing)?|coexist(?:s|ed|ing)?|"
        r"display(?:s|ed|ing)?|show|shows|showed|shown|connect(?:s|ed|ing)?|"
        r"compar(?:e|es|ed|ing)|mov(?:e|es|ed|ing)|come|comes|came|put)", re.I)

    def linked_cue(first, second, cue, text):
        if first[0] <= cue.start() < second[1]:
            return cue.group().casefold() == "versus"
        if cue.end() <= first[0]:
            return (re.fullmatch(r"both|compar\w*", cue.group(), re.I) is not None
                    and re.fullmatch(r"\s*(?:the\s+)?", text[cue.end():first[0]], re.I) is not None)
        if cue.start() < second[1]:
            return False
        bridge = text[second[1]:cue.start()]
        if re.search(r"[^\w\s,]|,(?!\s*then\b)", bridge, re.I):
            return False
        words = tokens.findall(bridge)
        return len(words) <= 12 and all(predicate_words.fullmatch(word) for word in words)

    candidates = []
    for offset, limit in spans:
        text = source[offset:limit]
        owned_cues = [cue for cue in relationship.finditer(text) if begin <= offset + cue.start() < end]
        if not owned_cues:
            continue
        if re.search(r"\b(?:without|not\s+about|unrelated\s+to|no\s+mention\s+of|excluding)\b", text, re.I):
            continue
        if re.search(r"\b(?:not|never|cannot|can't|doesn't|don't|didn't|no)\b[^.!?;]{0,24}\b(?:depend|together|both|compar)", text, re.I):
            continue
        # Dependency subjects may be abstract (for example thickness/range).
        # Keep their exact short names instead of inventing catalogue objects.
        dependency = re.search(r"\bdepend\w*\s+on\s+(?:the\s+)?(.+?)\s+and\s+(?:the\s+)?(.+?)(?:[.;!?؟]|$)", text, re.I)
        if dependency and any(cue.start() == dependency.start() for cue in owned_cues):
            names = [" ".join(value.split()).strip(" ,:;.!?؟") for value in dependency.groups()]
            if all(1 <= len(name.split()) <= 4 and len(name) <= 48
                   and not re.search(r"\b(?:not|no|never|without|if|whether|when|because|can|could|would|should|is|are|was|were|and|or|but|then)\b", name, re.I)
                   for name in names):
                candidates.append(names)
        # Otherwise accept only coordinated literal catalogue names. The
        # relation may span a cut, but a distant incidental noun is not cast.
        hits = []
        for symbol, expression in _LITERAL_LABELS.items():
            for match in re.finditer(r"\b(?:" + expression + r")\b", text, re.I):
                if symbol_supported(symbol, text):
                    hits.append((match.start(), match.end(), match.group()))
        hits.sort(key=lambda row: (row[0], -(row[1] - row[0])))
        unique = []
        for hit in hits:
            if not unique or hit[0] >= unique[-1][1]:
                unique.append(hit)
        for first, second in zip(unique, unique[1:]):
            if (re.fullmatch(r"\s+(?:and|versus)\s+(?:the\s+)?", text[first[1]:second[0]], re.I)
                    and any(linked_cue(first, second, cue, text) for cue in owned_cues)):
                candidates.append([first[2], second[2]])
    unique_pairs = {tuple(name.casefold() for name in names): names for names in candidates}
    if len(unique_pairs) != 1:
        return []
    names = next(iter(unique_pairs.values()))
    if names[0].casefold() == names[1].casefold():
        return []
    return [{"label": name[0].upper() + name[1:], "symbol": symbol_for_item(name, source),
             "role": "subject" if index == 0 else "support"} for index, name in enumerate(names)]


def symbol_supported(symbol, narration, *, label=None):
    if symbol == "concept":
        # Only a complete literal label makes this a substitution. Do not turn
        # "battery costs" or "armour limits" into physical objects, nor borrow
        # an actor that is absent from the current shot's narration. Material
        # geometry has its own grounding path; its concept part labels do not
        # go through this ordinary actor check.
        name = re.sub(r"^(?:a|an|the|this|that|these|those)\s+", "",
                      " ".join(str(label or "").split()).strip(" .,:;!?"), flags=re.I)
        return not any(re.fullmatch(expression, name, re.I) and symbol_supported(subject, narration)
                       for subject, expression in _LITERAL_LABELS.items())
    expression = dict(_RULES).get(symbol)
    if symbol == "mining" and re.search(r"\bdata\s+mining\b", str(narration), re.I) and not re.search(r"\b(?:ore|mines?|minerals?|drill\w*)\b", str(narration), re.I):
        return False
    source = _source_text(narration)
    if symbol in {"armour", "firearm"}:
        # A named absence is not a depicted object. Keep this bounded to the
        # new physical subjects rather than treating "no guarantee" as a ban
        # on every concrete noun later in the same sentence.
        source = re.sub(r"\bno\s+" + expression + r"[^.!?;]*", "", source, flags=re.I)
    return bool(expression and re.search(expression, source, re.I))


def grounding_issues(scene, narration, *, context=""):
    """Check depicted actors/actions against this scene, never the whole film.

    These lexical checks catch unsupported subjects and mechanisms. They do not
    establish that a claim is true, or pretend to be a perceptual review.
    """
    source = _source_text(narration)
    # The enclosing sentence contributes only the two explicit subject names;
    # world/action evidence remains the owned narration excerpt below.
    actor_source = source + " " + " ".join(item["label"] for item in paired_subjects(narration, context))
    issues = []
    staging = validate_staging(scene.get("staging"))
    world, action = staging["world"], staging["action"]
    if world in _WORLD_EVIDENCE and not re.search(_WORLD_EVIDENCE[world], source, re.I):
        issues.append("The selected setting is not supported by this scene's narration.")
    if world == "mining" and not symbol_supported("mining", source):
        issues.append("The physical mining setting is not supported by this scene's narration.")
    qualitative_comparison = action == "compare" and scene.get("template") == "comparison" and len(scene.get("items", [])) >= 2
    if action in _ACTION_EVIDENCE and not qualitative_comparison and not re.search(_ACTION_EVIDENCE[action], source, re.I):
        issues.append("The selected action is not supported by this scene's narration.")
    for item in scene.get("items", []):
        if not symbol_supported(item.get("symbol"), actor_source, label=item.get("label")):
            issues.append("A named catalogue subject needs its supported actor instead of an abstract concept."
                          if item.get("symbol") == "concept" else
                          "A depicted actor is not supported by this scene's narration.")
    if world == "census" and any(item.get("role") == "subject" and item.get("symbol") not in {"census", "census-gap", "people", "document"} for item in scene.get("items", [])):
        issues.append("The census setting needs a census, people or records subject.")
    if world == "flood" and action in {"adapt", "float"}:
        subject = next((item for item in scene.get("items", []) if item.get("role") == "subject"), {})
        if subject.get("symbol") == "water" and (symbol_supported("people", source) or symbol_supported("building", source)):
            issues.append("The subject emphasizes incidental water instead of the narrated adaptation.")
    return list(dict.fromkeys(issues))


def local_staging(narration, template="explain", *, calm=False):
    source = _source_text(narration)
    world = next((name for name, expression in _WORLD_EVIDENCE.items()
                  if re.search(expression, source, re.I)), "generic")
    if world == "mining" and not symbol_supported("mining", source):
        world = "generic"
    if world == "flood":
        action = ("float" if re.search(_ACTION_EVIDENCE["float"], source, re.I) else
                  "adapt" if re.search(_ACTION_EVIDENCE["adapt"], source, re.I) else "rise")
    elif world == "census":
        action = "gap" if re.search(_ACTION_EVIDENCE["gap"], source, re.I) else "record"
    elif world == "mining":
        action = "transfer" if re.search(_ACTION_EVIDENCE["transfer"], source, re.I) else "extract"
    elif template == "comparison" and re.search(_ACTION_EVIDENCE["compare"], source, re.I):
        action = "compare"
    elif re.search(_ACTION_EVIDENCE["assemble"], source, re.I):
        action = "assemble"
    elif re.search(_ACTION_EVIDENCE["connect"], source, re.I):
        action = "connect"
    else:
        action = "reveal"
    camera = {"rise": "wide", "adapt": "follow", "float": "wide", "record": "reveal", "gap": "detail",
              "extract": "detail", "transfer": "follow", "assemble": "reveal",
              "connect": "follow", "compare": "wide", "reveal": "wide"}[action]
    return {"version": 1, "world": world, "action": action, "camera": "locked" if calm else camera}


def _actor_label(symbol, source, fallback):
    expressions = {
        "solar": r"\bsolar\s+panels?\b|\bsunlight\b|\bsolar\b",
        "building": r"\b(?:floating|raised|flooded)\s+(?:homes?|houses?|buildings?)\b|" + dict(_RULES)["building"],
        "people": r"\b(?:rescue|construction|census)\s+workers?\b|" + dict(_RULES)["people"],
        "armour": r"\b(?:body\s+)?armou?r\b|\bbreastplates?\b|\bcuirass(?:es)?\b",
    }
    expression = expressions.get(symbol, dict(_RULES).get(symbol))
    match = re.search(expression, source, re.I) if expression else None
    label = match.group(0) if match else fallback
    return " ".join(label.split()[:6])[:64].strip().capitalize()


def apply_staging(scene, narration, *, calm=False):
    """A labelled local fallback, used only for new or explicitly rebuilt scenes."""
    if scene["template"] in {"chart", "footage"}:
        return scene
    staging = local_staging(narration, scene["template"], calm=calm)
    items, seen = [], set()
    for original in scene["items"]:
        label = original["label"]
        if label == "Explore the explanation":
            continue
        symbol = symbol_for_item(label, narration)
        if staging["world"] == "flood" and staging["action"] in {"adapt", "float"}:
            for candidate in ("building", "people") if staging["action"] == "float" else ("people", "building"):
                if re.search(dict(_RULES)[candidate], label, re.I) and symbol_supported(candidate, narration):
                    symbol = candidate
                    break
        # Two copies of the same rain animation are not two different actors.
        # Actual comparisons may legitimately show alternatives of one kind.
        identity = (symbol, label.casefold()) if symbol == "concept" or scene["template"] == "comparison" else symbol
        if identity in seen:
            continue
        seen.add(identity)
        items.append({"label": _actor_label(symbol, _source_text(narration), label), "symbol": symbol})
    source = _source_text(narration)
    if staging["world"] == "generic" and any(
            symbol != "concept" and symbol_supported(symbol, source) for symbol in SYMBOLS):
        # Narration fragments are planning fallbacks, not additional objects.
        # Reserve the bounded cast for the actual supported subjects before
        # applying the count limit; four fragments must not crowd them out.
        items = [item for item in items if item["symbol"] != "concept"]
    items = items[:4]
    relevant = {"flood": {"water", "building", "people"},
                "census": {"census", "census-gap", "people", "document"},
                "mining": {"mining", "trade", "people", "building"}}.get(staging["world"], set(SYMBOLS))
    for symbol, expression in _RULES:
        if symbol not in relevant or any(item["symbol"] == symbol for item in items) or len(items) >= 4:
            continue
        # Data mining is not evidence of a mine, drill or physical ore.
        if symbol == "mining" and re.search(r"\bdata\s+mining\b", source, re.I) and not re.search(r"\b(?:ore|mines?|minerals?|drill\w*)\b", source, re.I):
            continue
        match = re.search(expression, source, re.I)
        if match and symbol_supported(symbol, source):
            items.append({"label": _actor_label(symbol, source, match.group(0)), "symbol": symbol})
    if any(item["symbol"] != "concept" for item in items):
        # A fallback heading such as "Scene 1" is not another narrated actor.
        items = [item for item in items if item["symbol"] != "concept" or _words(item["label"]) & _words(source)]
    if staging["action"] in {"connect", "assemble", "transfer"}:
        def mentioned(item):
            match = re.search(re.escape(item["label"]), source, re.I)
            return match.start() if match else len(source)
        items.sort(key=mentioned)
    if staging["world"] == "flood" and staging["action"] == "adapt":
        # A long opening clause can otherwise consume a truncated label before
        # it reaches the people who are doing the adapting.
        if symbol_supported("people", narration) and not any(item["symbol"] == "people" for item in items):
            match = re.search(dict(_RULES)["people"], _source_text(narration), re.I)
            items = items[:3] + [{"label": match.group(0).capitalize(), "symbol": "people"}]
    if not items:
        items = [{"label": scene["title"], "symbol": "concept"}]
    priorities = {
        ("flood", "adapt"): ("people", "building", "concept", "water"),
        ("flood", "float"): ("building", "people", "concept", "water"),
        ("flood", "rise"): ("water", "building", "people", "concept"),
        ("census", "record"): ("census", "people", "document", "concept"),
        ("census", "gap"): ("census-gap", "people", "document", "concept"),
        ("mining", "extract"): ("mining", "people", "concept"),
        ("mining", "transfer"): ("mining", "trade", "people", "concept"),
    }.get((staging["world"], staging["action"]), ())
    subject = next((index for symbol in priorities for index, item in enumerate(items) if item["symbol"] == symbol), 0)
    for index, item in enumerate(items):
        item["role"] = ("subject" if index == subject else "context"
                        if staging["world"] == "flood" and item["symbol"] == "water" else "support")
    scene.update(graphics_version=2, staging=staging, items=items)
    return scene


def validate_actors(items):
    if (not isinstance(items, list) or not 1 <= len(items) <= 4
            or any(not isinstance(item, dict) or item.get("symbol") not in SYMBOLS or item.get("role") not in ROLES
                   for item in items)
            or sum(item["role"] == "subject" for item in items) != 1):
        raise ValueError("A staged scene needs one subject and up to three grounded support/context actors.")
    return deepcopy(items)


def explicit_staging(instructions, current, narration, *, calm=False):
    """Small, visible editing controls; never invent a requested mechanism."""
    from agents.art_direction import positive_instruction_text
    text = positive_instruction_text(instructions)
    result = dict(current or local_staging(narration, calm=calm))
    changed = False
    cameras = (("detail", r"\b(?:camera\s+(?:closer|close)|closer\s+(?:camera|view)|close[ -]?up|zoom\s+in|detail\s+(?:view|camera))\b"),
               ("wide", r"\b(?:wide\s+(?:shot|view|camera)|zoom\s+out|camera\s+(?:wider|back))\b"),
               ("locked", r"\b(?:locked|static|fixed)\s+(?:shot|view|camera)\b"),
               ("follow", r"\b(?:follow(?:ing)?\s+(?:shot|camera|the\s+subject)|camera\s+follow\w*)\b"),
               ("reveal", r"\b(?:reveal\s+(?:shot|camera)|camera\s+reveal)\b"))
    for camera, expression in cameras:
        if re.search(expression, text, re.I):
            result["camera"], changed = camera, True
            break
    for action in STAGING_ACTIONS[result["world"]]:
        if re.search(r"\b(?:action\s*(?:to|:)?\s*" + action + r"|" + action + r"\s+action)\b", text, re.I):
            candidate = {"staging": {**result, "action": action}, "items": []}
            if grounding_issues(candidate, narration):
                raise ValueError("That action is not supported by this scene's narration. Choose a supported camera or palette, or revise the script first.")
            result["action"], changed = action, True
            break
    if calm:
        result["camera"] = "locked"
    return result if changed else None
