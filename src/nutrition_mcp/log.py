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


def _chain(error: BaseException | None) -> list[BaseException]:
    """The exception and the ones behind it, original first, as Python's traceback shows them.

    The SDK logs a tool crash as UnexpectedToolError raised "from" the real error, so without
    the chain the log would name only the wrapper and show only SDK frames.
    """
    links: list[BaseException] = []
    # By identity, so a cycle in the chain cannot loop forever.
    while error is not None and all(error is not seen for seen in links):
        links.append(error)
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return links[::-1]


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
        if record.exc_info:
            # A KeyError's message is the key that was asked for, so each exception keeps
            # only its type and its frames (file, line and code, no values).
            line["exceptions"] = [
                {
                    "type": type(error).__name__,
                    "stack": "".join(traceback.format_tb(error.__traceback__)),
                }
                for error in _chain(record.exc_info[1])
            ]
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
