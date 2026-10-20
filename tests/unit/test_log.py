import json
import logging
import sys

import pytest

from nutrition_mcp.log import JsonFormatter, configure_logging


def record(msg: str, **extra: object) -> logging.LogRecord:
    entry = logging.LogRecord("nutrition_mcp.server", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(entry, key, value)
    return entry


def test_a_tool_call_is_one_json_line_with_its_fields() -> None:
    line = JsonFormatter().format(
        record("tool call", tool="get_nutrition", latency_ms=1.25, outcome="ok", count=1)
    )
    assert "\n" not in line
    entry = json.loads(line)
    assert entry["level"] == "INFO"
    assert entry["msg"] == "tool call"
    assert entry["ts"].endswith("+00:00")
    assert (entry["tool"], entry["latency_ms"], entry["outcome"], entry["count"]) == (
        "get_nutrition",
        1.25,
        "ok",
        1,
    )


def test_a_plain_line_has_no_call_fields() -> None:
    entry = json.loads(JsonFormatter().format(record("started")))
    assert set(entry) == {"ts", "level", "msg"}


def test_an_exception_keeps_its_type_and_stack_but_not_its_message() -> None:
    # A KeyError from a lookup carries the ID that was asked for, which is a tool input. The
    # stack shows source lines, so the ID is held in a variable as it would be in a tool.
    food_id = "".join(["secret", "_food"])
    try:
        {}[food_id]
    except KeyError:
        entry = record("crash")
        entry.exc_info = sys.exc_info()
    line = JsonFormatter().format(entry)
    assert "secret_food" not in line
    [logged] = json.loads(line)["exceptions"]
    assert logged["type"] == "KeyError"
    assert "test_log.py" in logged["stack"]


def _wrapped_crash() -> logging.LogRecord:
    # The SDK logs a tool crash as UnexpectedToolError raised "from" the original error.
    detail = "".join(["secret", "_food"])
    try:
        try:
            raise RuntimeError(detail)
        except RuntimeError as err:
            raise LookupError("wrapper") from err
    except LookupError:
        entry = record("crash")
        entry.exc_info = sys.exc_info()
    return entry


def test_an_exception_chain_is_logged_cause_first_without_messages() -> None:
    line = JsonFormatter().format(_wrapped_crash())
    assert "secret_food" not in line
    chain = json.loads(line)["exceptions"]
    assert [link["type"] for link in chain] == ["RuntimeError", "LookupError"]
    # Each link keeps its own frames, so the original error's frames are not lost.
    assert "raise RuntimeError(detail)" in chain[0]["stack"]
    assert "raise LookupError" in chain[1]["stack"]


def test_an_exception_raised_while_handling_another_keeps_both() -> None:
    try:
        try:
            raise KeyError("first")
        except KeyError:
            raise ValueError("second") from None
    except ValueError:
        suppressed = record("crash")
        suppressed.exc_info = sys.exc_info()
    try:
        try:
            raise KeyError("first")
        except KeyError:
            raise ValueError("second")  # noqa: B904
    except ValueError:
        implicit = record("crash")
        implicit.exc_info = sys.exc_info()
    # "from None" hides the first error, as Python's own traceback does.
    assert [
        link["type"] for link in json.loads(JsonFormatter().format(suppressed))["exceptions"]
    ] == ["ValueError"]
    assert [
        link["type"] for link in json.loads(JsonFormatter().format(implicit))["exceptions"]
    ] == [
        "KeyError",
        "ValueError",
    ]


@pytest.mark.usefixtures("restore_logging")
def test_configure_logging_writes_json_to_stderr_and_quiets_the_sdk(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("debug")
    logging.getLogger("nutrition_mcp.anything").debug("hello")
    # The SDK's INFO line for a failed call repeats the error text, which can hold inputs.
    logging.getLogger("mcp.server.mcpserver.server").info("Tool %r failed: %r", "t", "secret")
    logging.getLogger("mcp.server.mcpserver.server").warning("Tool already exists: t")
    out, err = capsys.readouterr()
    assert out == ""
    assert [json.loads(line)["msg"] for line in err.splitlines()] == [
        "hello",
        "Tool already exists: t",
    ]
