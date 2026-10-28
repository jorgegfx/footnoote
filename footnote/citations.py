"""Deterministic handling of `[n]` citation markers in answer text."""

import re

# `[3]`, `[1, 2]`, `[1,2,4]`; not `[text](url)` links, which never start with a digit run + `]`.
_MARKER = re.compile(r"\s?\[(\d+(?:\s*,\s*\d+)*)\](?!\()")


def clean_citations(text: str, source_count: int) -> tuple[str, list[int]]:
    """Drop markers that point outside 1..source_count and normalize `[1, 2]` to `[1][2]`.

    Returns the cleaned text and the sorted, unique indices it still cites.
    """
    used: set[int] = set()

    def replace(match: re.Match[str]) -> str:
        indices = [int(n) for n in re.findall(r"\d+", match.group(1))]
        valid = list(dict.fromkeys(i for i in indices if 1 <= i <= source_count))
        if not valid:
            return ""
        used.update(valid)
        leading = match.group(0)[0] if match.group(0)[0].isspace() else ""
        return leading + "".join(f"[{i}]" for i in valid)

    return _MARKER.sub(replace, text), sorted(used)
