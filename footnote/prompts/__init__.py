"""Versioned prompt files and the helpers that put retrieved content into prompts.

Nodes load prompts by name; no prompts inline in code.
"""

import html
from collections.abc import Sequence
from functools import cache
from importlib.resources import files
from string import Template

from footnote.models import SourceResult
from footnote.text import truncate


@cache
def load_prompt(name: str) -> Template:
    """`$placeholders` are filled with `Template.substitute`, so literal braces are safe."""
    return Template(files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8"))


def source_blocks(
    question: str, sources: Sequence[SourceResult], snippet_chars: int | None = None
) -> str:
    """Question plus one delimited `<source index="n">` block per result, numbered from 1.

    Content is escaped so retrieved text can't close a block or forge another source.
    """
    blocks = [f"Question: {html.escape(question, quote=False)}", ""]
    for index, source in enumerate(sources, start=1):
        attrs = {
            "index": str(index),
            "outlet": source.venue or source.source_id,
            "date": source.published_at.isoformat() if source.published_at else "unknown",
        }
        snippet = source.snippet
        if snippet_chars is not None:
            snippet = truncate(snippet, snippet_chars)
        attr_text = " ".join(f'{k}="{html.escape(v)}"' for k, v in attrs.items())
        blocks += [
            f"<source {attr_text}>",
            f"Title: {html.escape(source.title, quote=False)}",
            html.escape(snippet, quote=False) or "(no snippet available)",
            "</source>",
            "",
        ]
    return "\n".join(blocks).rstrip()
