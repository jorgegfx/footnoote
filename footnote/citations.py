"""Deterministic handling of `[n]` citation markers in answer text."""

import re
from collections.abc import Callable

# `[3]`, `[1, 2]`, `[1,2,4]`; not `[text](url)` links, which never start with a digit run + `]`.
_MARKER = re.compile(r"\s?\[(\d+(?:\s*,\s*\d+)*)\](?!\()")
# Adjacent markers (`[1][2]`, `[1] [2, 3]`), rewritten as one unit so the run keeps its space.
_RUN = re.compile(r"\s?\[\d+(?:\s*,\s*\d+)*\](?!\()(?:\s*\[\d+(?:\s*,\s*\d+)*\](?!\())*")
# End of a sentence: terminal punctuation, any markers placed after it, then space or the end.
_SENTENCE_END = re.compile(r"[.!?](?:\s*\[\d+\])*(?=\s|$)")
# A line left holding only a list bullet (or nothing) after its sentences were removed.
_EMPTY_LINE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*$")


def _rewrite_markers(text: str, keep: Callable[[int], bool]) -> str:
    """Rewrite each marker run as `[a][b]`: unique, in order, only indices `keep` accepts."""

    def replace(match: re.Match[str]) -> str:
        found = (int(n) for n in re.findall(r"\d+", match.group(0)))
        indices = [i for i in dict.fromkeys(found) if keep(i)]
        if not indices:
            return ""
        leading = match.group(0)[0] if match.group(0)[0].isspace() else ""
        return leading + "".join(f"[{i}]" for i in indices)

    return _RUN.sub(replace, text)


def clean_citations(text: str, source_count: int) -> tuple[str, list[int]]:
    """Drop markers that point outside 1..source_count and normalize `[1, 2]` to `[1][2]`.

    Returns the cleaned text and the sorted, unique indices it still cites.
    """
    text = _rewrite_markers(text, lambda i: 1 <= i <= source_count)
    return text, sorted(set(cited_indices(text)))


def split_sentences(text: str) -> list[str]:
    """Split text into sentences that concatenate back to `text` exactly.

    Each piece keeps its trailing whitespace, so removing a piece leaves the rest intact.
    Line breaks always end a sentence, which keeps list items and paragraphs separate.
    """
    pieces: list[str] = []
    for line in text.splitlines(keepends=True):
        start = 0
        for match in _SENTENCE_END.finditer(line):
            end = match.end()
            while end < len(line) and line[end].isspace():
                end += 1
            pieces.append(line[start:end])
            start = end
        if start < len(line):
            pieces.append(line[start:])
    return pieces


def cited_indices(sentence: str) -> list[int]:
    """Unique citation indices in `sentence`, in order of appearance."""
    found = (int(n) for m in _MARKER.finditer(sentence) for n in re.findall(r"\d+", m.group(1)))
    return list(dict.fromkeys(found))


def keep_citations(sentence: str, keep: set[int]) -> str:
    """Remove every marker in `sentence` whose index isn't in `keep`."""
    return _rewrite_markers(sentence, keep.__contains__)


def tidy(text: str) -> str:
    """Collapse what sentence removal leaves behind: bare bullets, runs of blank lines."""
    lines = [line.rstrip() for line in text.splitlines()]
    kept: list[str] = []
    for line in lines:
        if _EMPTY_LINE.match(line):
            if kept and kept[-1] != "":
                kept.append("")
            continue
        kept.append(line)
    return "\n".join(kept).strip()
