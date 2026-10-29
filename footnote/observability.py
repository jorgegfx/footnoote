"""Structured logging: request ids, JSON or text output, and API-key redaction."""

import json
import logging
import re
from collections.abc import Iterable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# Credentials that show up in URLs and headers: `api-key=…`, `api_key=…`, `apikey=…`,
# `key=…`, `Bearer …`.
_KEY_PARAMS = re.compile(r"(?i)\b((?:api[-_]?key|key|token)=)[^&\s\"']+")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+")
REDACTED = "[REDACTED]"

# Fields `logger.info(..., extra={...})` may add that formatters should show.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    text = _KEY_PARAMS.sub(rf"\1{REDACTED}", text)
    return _BEARER.sub(rf"\1{REDACTED}", text)


class RedactingFilter(logging.Filter):
    """Replaces known secret values and key-shaped URL parameters in every record."""

    def __init__(self, secrets: Iterable[str] = ()):
        super().__init__()
        self.secrets = sorted({s for s in secrets if len(s) >= 6}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self.secrets)
        record.args = None
        if record.exc_info:
            record.exc_text = redact(
                logging.Formatter().formatException(record.exc_info), self.secrets
            )
            record.exc_info = None
        record.request_id = request_id.get()
        return True


def extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry |= {k: v for k, v in extra_fields(record).items() if v is not None}
        if record.exc_text:
            entry["exc"] = record.exc_text
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = {k: v for k, v in extra_fields(record).items() if v is not None}
        if extras:
            line += " " + " ".join(f"{k}={json.dumps(v, default=str)}" for k, v in extras.items())
        return line


def configure_logging(level: str = "INFO", fmt: str = "text", secrets: Iterable[str] = ()) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(RedactingFilter(secrets))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # httpx logs every request URL at INFO; query strings can carry API keys.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
