"""Versioned prompt files. Nodes load them by name; no prompts inline in code."""

from functools import cache
from importlib.resources import files
from string import Template


@cache
def load_prompt(name: str) -> Template:
    """`$placeholders` are filled with `Template.substitute`, so literal braces are safe."""
    return Template(files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8"))
