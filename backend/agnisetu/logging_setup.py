"""Structured JSON logging with a per request correlation id."""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import re
import sys

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_MAP_KEY_IN_PATH = re.compile(r"(/api/(?:area|data_availability)/csv/)[A-Za-z0-9]{32}")
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = _MAP_KEY_IN_PATH.sub(r"\1<MAP_KEY>", record.getMessage())
        payload = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": message,
            "request_id": request_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = _MAP_KEY_IN_PATH.sub(r"\1<MAP_KEY>", self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_agnisetu_configured", False):
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.handlers[:] = [handler]
    root.setLevel(level)
    root._agnisetu_configured = True  # type: ignore[attr-defined]
    # httpx logs full request URLs at INFO, which would include the MAP_KEY path segment.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True
