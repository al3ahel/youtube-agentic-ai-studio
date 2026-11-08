"""
Tolerant JSON extraction for LLM output.
Strips markdown fences, finds the outermost object, and repairs the most
common model mistakes (unclosed brackets, trailing commas) before parsing.
"""
import json
import math
import re


class _StrictJSONError(ValueError):
    """A fixed diagnostic that never contains model-supplied text."""


def extract_complete_json(raw: str, label: str = "Model") -> dict:
    """Parse a complete JSON object without inventing missing model output.

    Scripts must not silently acquire a closing quote or lose half a section.
    A single enclosing Markdown fence is harmless; partial objects, duplicate
    keys, nonfinite numbers and additional documents are rejected. Keep the
    older tolerant extractor below for its existing callers.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} returned an empty JSON response.")
    if len(raw) > 200_000:
        raise ValueError(f"{label} JSON response exceeds the supported size.")
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise _StrictJSONError("duplicate object keys are not allowed")
            result[key] = value
        return result

    def constant(_):
        raise _StrictJSONError("numbers must be finite")

    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise _StrictJSONError("numbers must be finite")
        return parsed

    try:
        result = json.loads(text, object_pairs_hook=object_pairs,
                            parse_constant=constant, parse_float=number)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} returned invalid or incomplete JSON "
                         f"(line {exc.lineno}, column {exc.colno}).") from None
    except _StrictJSONError as exc:
        raise ValueError(f"{label} returned invalid JSON: {exc}.") from None
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise ValueError(f"{label} returned JSON outside the supported limits.") from None
    if not isinstance(result, dict):
        raise ValueError(f"{label} must return one complete JSON object.")
    try:
        json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(f"{label} returned text that cannot be saved as UTF-8 JSON.") from None
    return result


def _close_brackets(s: str) -> str:
    """Insert missing ] / } so every opener is closed in the right order."""
    out, stack, in_str, esc = [], [], False, False
    pairs = {"{": "}", "[": "]"}
    for ch in s:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in "]}":
            # Close any inner structures the model forgot before this closer
            while stack and stack[-1] != ch:
                out.append(stack.pop())
            if stack:
                stack.pop()
            else:
                continue          # stray closer — drop it
        out.append(ch)
    if in_str:
        out.append('"')
    out.extend(reversed(stack))
    return "".join(out)


def extract_json(raw: str, label: str = "Model") -> dict:
    text = re.sub(r"^```(?:json)?", "", raw.strip()).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError(f"{label} returned no JSON.\nRaw:\n{raw[:600]}")
    end = text.rfind("}")
    candidate = text[start:end + 1] if end > start else text[start:]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    repaired = re.sub(r",\s*([}\]])", r"\1", _close_brackets(text[start:]))
    # Drop anything after the top-level object closes
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(repaired):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                repaired = repaired[:i + 1]
                break
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as e:
        raise ValueError(f"{label} returned invalid JSON ({e}).\nRaw:\n{raw[:600]}") from e
