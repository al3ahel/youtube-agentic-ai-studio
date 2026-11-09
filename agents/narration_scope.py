"""Scope source narration to a shot without treating subtitle chunks as cuts.

Timed words belong to the shot in which they start. Exact lexical alignment to
the original section restores punctuation without importing another shot's
words. The surrounding sentence is kept separately for questions and negation;
it never expands the current shot's narrated action requirements.
"""
from __future__ import annotations

import re


_TOKENS = re.compile(r"\w+(?:['’]\w+)*")


def _normalise(token):
    return token.casefold().replace("’", "'")


def _sentence_context(text, first, last):
    """Return only original sentences intersecting the selected token span."""
    boundaries = [0, *(match.end() for match in re.finditer(r"[.!?؟][\"'’”)\]]*\s+", text)), len(text)]
    begin = max(boundary for boundary in boundaries if boundary <= first)
    end = next(boundary for boundary in boundaries if boundary > last)
    return text[begin:end].strip()


def scene_source(section, start, end, captions=(), words=()):
    """Return source text and its scope; do not mutate any timeline input.

    ``source_narration`` is the current-shot excerpt. ``source_sentence_context``
    is grammatical context only. ``source_section_narration`` retains the full
    original source for audit. ``source_alignment`` is exact, unverified, or
    unavailable; an unverified timed source must not establish semantic review
    approval. Captions/duration remain explicitly labeled legacy fallbacks when
    this section has no word timing at all.
    """
    narration = section["narration"]
    result = {"source_narration": "", "source_sentence_context": "",
              "source_section_narration": narration, "source_alignment": "unavailable"}
    section_start = section.get("speech_start", section["start"])
    section_end = section.get("speech_end", section["start"] + section["duration"])
    measured = sorted((word for word in words
                       if (str(word["section_id"]) == str(section["id"]) if "section_id" in word
                           else section_start <= word["start"] < section_end)),
                      key=lambda word: (word["start"], word["end"]))
    if measured:
        # Whole-section equality is deliberate: a partial/ambiguous match must
        # not attach a question mark, a negation, or another sentence's action.
        source_tokens = list(_TOKENS.finditer(narration))
        timed_tokens = [(token, word) for word in measured for token in _TOKENS.findall(word["word"])]
        aligned = bool(source_tokens) and [_normalise(match.group()) for match in source_tokens] == [
            _normalise(token) for token, _ in timed_tokens]
        owned = [word for word in measured if start <= word["start"] < end]
        result["source_alignment"] = "exact" if aligned else "unverified"
        if not owned:
            # A silent hold cannot borrow the next sentence through an
            # overlapping subtitle or through duration-proportional guessing.
            result["source_scope"] = "word_timing_gap"
            return result
        result["source_word_window"] = {"start": owned[0]["start"], "end": owned[-1]["end"]}
        result["source_scope"] = "word_timing" if aligned else "word_timing_unaligned"
        if aligned:
            indices = [index for index, (_, word) in enumerate(timed_tokens) if start <= word["start"] < end]
            if not indices:
                result["source_scope"] = "word_timing_gap"
                return result
            first, last = indices[0], indices[-1]
            text_end = source_tokens[last + 1].start() if last + 1 < len(source_tokens) else len(narration)
            text_start = source_tokens[first].start() if first else 0
            result["source_narration"] = narration[text_start:text_end].strip()
            result["source_sentence_context"] = _sentence_context(
                narration, source_tokens[first].start(), source_tokens[last].end() - 1)
        else:
            result["source_narration"] = " ".join(word["word"] for word in owned)
        return result

    matches = [caption["text"] for caption in captions
               if caption["start"] < end - .00001 and caption["end"] > start + .00001]
    if matches:
        result.update(source_narration=" ".join(matches), source_scope="caption_overlap")
        return result
    tokens = narration.split()
    duration = section["duration"]
    begin = max(0, min(len(tokens), round(len(tokens) * (start - section["start"]) / duration)))
    finish = max(begin + 1, min(len(tokens), round(len(tokens) * (end - section["start"]) / duration)))
    result.update(source_narration=" ".join(tokens[begin:finish]) or narration,
                  source_scope="section_excerpt_by_duration")
    return result
