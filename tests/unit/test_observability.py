import json
import logging

import pytest

from footnote.observability import (
    REDACTED,
    JsonFormatter,
    RedactingFilter,
    TextFormatter,
    redact,
    request_id,
)

SECRET = "sk-live-abcdef123456"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("GET /x?api-key=abc123&q=1", f"GET /x?api-key={REDACTED}&q=1"),
        ("https://a.org/?api_key=abc&x=2", f"https://a.org/?api_key={REDACTED}&x=2"),
        ("?apikey=abc", f"?apikey={REDACTED}"),
        ("?key=abc", f"?key={REDACTED}"),
        ("Authorization: Bearer abc.def-ghi", f"Authorization: Bearer {REDACTED}"),
        (f"provider said {SECRET}", f"provider said {REDACTED}"),
        ("nothing to hide", "nothing to hide"),
    ],
)
def test_redact(text: str, expected: str) -> None:
    assert redact(text, [SECRET]) == expected


def record(msg: str, *args: object, exc: BaseException | None = None) -> logging.LogRecord:
    exc_info = (type(exc), exc, exc.__traceback__) if exc else None
    return logging.LogRecord("footnote.test", logging.ERROR, __file__, 1, msg, args, exc_info)


def test_filter_redacts_message_args_and_tracebacks() -> None:
    try:
        raise RuntimeError(f"bad key {SECRET}")
    except RuntimeError as exc:
        rec = record("calling %s", f"https://x.org/?api-key={SECRET}", exc=exc)

    token = request_id.set("req-1")
    try:
        assert RedactingFilter([SECRET]).filter(rec)
    finally:
        request_id.reset(token)

    assert SECRET not in rec.getMessage()
    assert rec.exc_text is not None
    assert SECRET not in rec.exc_text
    assert "RuntimeError" in rec.exc_text
    assert rec.request_id == "req-1"  # type: ignore[attr-defined]


def test_json_formatter_includes_extras() -> None:
    rec = record("search completed")
    rec.timings_ms = {"total": 12}
    RedactingFilter().filter(rec)
    entry = json.loads(JsonFormatter().format(rec))
    assert entry["msg"] == "search completed"
    assert entry["level"] == "error"
    assert entry["timings_ms"] == {"total": 12}
    assert "request_id" not in entry  # None values are omitted


def test_text_formatter_appends_extras() -> None:
    rec = record("request")
    rec.status = 200
    line = TextFormatter().format(rec)
    assert line.endswith("footnote.test: request status=200")
