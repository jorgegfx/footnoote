"""Small parsing helpers shared by adapters."""

from datetime import date, datetime


def parse_iso_date(value: str | None) -> date | None:
    """Accepts `2026-10-01`, `2026-10-01T18:15:04Z` and `+0000`-style offsets."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip()).date()
    except ValueError:
        return None


def first_text(*values: str | None) -> str:
    return next((v.strip() for v in values if v and v.strip()), "")
