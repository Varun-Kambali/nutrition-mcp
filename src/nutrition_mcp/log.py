"""JSON log lines on stderr. stdout carries the MCP protocol, so nothing here writes to it.

A tool call adds `tool`, `latency_ms`, `outcome` and `count` to its record through `extra`.
No tool input is ever logged (ADR 0004), and the formatter drops exception messages, which
can repeat one.
"""

import json
import logging
import sys
import traceback
from datetime import UTC, datetime

CALL_FIELDS = ("tool", "latency_ms", "outcome", "count")


class JsonFormatter(logging.Formatter):
    """One JSON object per record: ts, level, msg, and the tool-call fields when present."""

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        for field in CALL_FIELDS:
            if hasattr(record, field):
                line[field] = getattr(record, field)
        if record.exc_info and record.exc_info[0] is not None:
            # A KeyError's message is the key that was asked for, so only the type and the
            # stack (file, line and code, no values) are kept.
            line["error"] = record.exc_info[0].__name__
            line["stack"] = "".join(traceback.format_tb(record.exc_info[2]))
        return json.dumps(line)


def configure_logging(level: str) -> None:
    """Send every log record to stderr as JSON, at `level` (a name such as INFO or debug)."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # The SDK logs a failed tool call at INFO with its error text, which repeats the input
    # ("unknown food id 'x'"), and at DEBUG it logs raw frames. Its warnings and errors stay.
    logging.getLogger("mcp").setLevel(logging.WARNING)
