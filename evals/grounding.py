"""Grounding evaluation: a model's nutrition answers with and without this server's tools.

Each question in evals/datasets/questions.jsonl lists the food ids, amounts and units behind
it. The golden answer is what `queries.meal_totals` computes for those items from the bundled
data, so a model that calls the tools and copies their totals matches it exactly. A question
about a dish that is not in the data has no items and names the dish in `expect_missing`.

The model answers every question twice with the same system prompt: once with no tools (from
memory), and once with the tools of a `nutrition-mcp` server started over stdio. The tool loop
is run here rather than by google-genai's automatic MCP support, so each `call_tool` can be
timed and its errors counted. The harness talks to the model through `ModelClient`, so tests
use a fake and only `GeminiClient` calls the network.

Scoring needs no judge model: the prompt asks for a final JSON object, and `parse_final_json`
reads it. An answer it cannot read is a failed answer, recorded and counted, never a crash.
"""

import contextlib
import json
import logging
import math
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import anyio
from mcp import Client, MCPError, StdioServerParameters
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from nutrition_mcp.config import Settings
from nutrition_mcp.loader import build_registry
from nutrition_mcp.queries import meal_totals
from nutrition_mcp.schemas import MealItemIn
from nutrition_mcp.server import TOOL_NAMES

if TYPE_CHECKING:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "evals" / "datasets" / "questions.jsonl"

DEFAULT_MODEL = "gemini-3.1-flash-lite"
# Temperature is left to the provider: Google advises keeping Gemini 3 models at their default
# of 1.0, because lower values can make them loop. Answers therefore vary from run to run.
TEMPERATURE = "default"
# Seconds between the starts of two model requests. Google publishes free-tier limits per
# project in AI Studio, not in its docs, so this is a conservative guess: 15 requests a minute.
MIN_REQUEST_INTERVAL_S = 4.0
# Model turns per question in the with-tools arm. Search, then compute, then answer takes 3.
MAX_TURNS = 8
# Waits before each retry of a request the API turned away as busy (429 or 5xx). Free-tier
# limits are counted per minute, so the waits add up to more than a minute.
RETRY_DELAYS = (5.0, 10.0, 20.0, 40.0)
TOOL_TIMEOUT_S = 30.0
# google-genai sets no request timeout of its own. A request that times out is retried.
REQUEST_TIMEOUT_MS = 120_000
# Questions in a row, across both arms, whose model request failed (an API error, or a request
# the API refused) before the run stops. By then the key, the quota, the model id or the
# service is at fault, and the rest would fail the same way.
MAX_CONSECUTIVE_API_ERRORS = 5
# Percentage error at or under which an answer counts as within 10%.
WITHIN_PCT = 10.0

# The keys of a final answer, and the meal total each one is scored against.
ANSWER_KEYS = ("kcal", "protein_g", "carbs_g")
GOLDEN_SOURCE = {"kcal": "kcal", "protein_g": "protein_g", "carbs_g": "carbs_available_g"}
METRIC_NAMES = {"kcal": "kcal", "protein_g": "protein", "carbs_g": "carbs"}

SYSTEM_PROMPT = (
    "You answer questions about the nutrition of Indian food. For everything the question "
    "lists, in the amounts it gives, work out the total energy in kcal, the total protein in "
    "grams and the total available carbohydrate in grams. Available carbohydrate is total "
    "carbohydrate minus dietary fibre. Give your best estimate when you are unsure of a "
    "value. End your reply with one JSON object and nothing after it, in this form: "
    '{"kcal": <number>, "protein_g": <number>, "carbs_g": <number>}. If a food in the '
    "question cannot be found, end instead with "
    '{"missing": ["<food as named in the question>"]} and give no numbers.'
)

# The server runs from the interpreter running the evaluation, the way the `nutrition-mcp`
# console script starts it, so both use the same installed package.
SERVER_MAIN = "from nutrition_mcp.server import main; main()"

Golden = dict[str, float]
# Each summary row's n for the two arms, Baseline first.
N_KEYS = ("n_without_tools", "n_with_tools")

log = logging.getLogger("nutrition_mcp.evals.grounding")


class GroundingError(Exception):
    """A problem with the questions file or a golden answer. The message names the row."""


# Questions and golden answers


class Question(BaseModel):
    """One row of the questions file: items for a golden answer, or dishes expected missing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    question: str
    items: tuple[MealItemIn, ...] = ()
    expect_missing: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _items_or_missing(self) -> "Question":
        if bool(self.items) == bool(self.expect_missing):
            raise ValueError("give either items or expect_missing, not both and not neither")
        return self


def load_questions(path: Path) -> list[Question]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as err:
        raise GroundingError(f"{path}: cannot read file: {err.strerror or err}") from err
    questions: list[Question] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            question = Question.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError) as err:
            raise GroundingError(f"{path}: line {number}: {err}") from None
        if question.id in seen:
            raise GroundingError(f"{path}: line {number}: second question with id {question.id!r}")
        seen.add(question.id)
        questions.append(question)
    return questions


def bundled_settings() -> Settings:
    """Server settings at their defaults, whatever NUTRITION_MCP_* variables are set.

    The with-tools arm starts the server without the caller's environment, so it runs on these
    defaults too. Arguments outrank environment variables in pydantic-settings.
    """
    fields = Settings.model_fields
    return Settings(
        data_dirs=[],
        default_katori_g=fields["default_katori_g"].default,
        log_level=fields["log_level"].default,
    )


def golden_answers(questions: Sequence[Question]) -> dict[str, Golden]:
    """kcal, protein and available carbohydrate for each question that lists items.

    Raises GroundingError when an item cannot be worked out, or when a total is missing,
    flagged incomplete or 0: a floor is no answer to score against, and 0 has no percentage
    error.
    """
    reg = build_registry([])
    settings = bundled_settings()
    golden: dict[str, Golden] = {}
    for question in questions:
        if not question.items:
            continue
        try:
            meal = meal_totals(reg, settings, list(question.items))
        except ValueError as err:
            raise GroundingError(f"question {question.id}: {err}") from err
        answer: Golden = {}
        for key, source in GOLDEN_SOURCE.items():
            value = meal.total.get(source)
            if value is None or source in meal.total_incomplete:
                raise GroundingError(f"question {question.id}: {source} is missing or incomplete")
            if value <= 0:
                raise GroundingError(f"question {question.id}: {source} is 0")
            answer[key] = value
        golden[question.id] = answer
    return golden


# Parsing the final answer


@dataclass(frozen=True, slots=True)
class Numbers:
    values: dict[str, float]


@dataclass(frozen=True, slots=True)
class Missing:
    foods: tuple[str, ...]
    # Numbers given next to the missing list; such an answer does not count as reporting it.
    has_numbers: bool


@dataclass(frozen=True, slots=True)
class Failed:
    reason: str
    # The kind of error ("http 400") when the API refused the request, which ends the answer.
    model_error: str | None = None


@dataclass(frozen=True, slots=True)
class ApiError:
    """No answer, because the API was busy or unreachable after the retries. Not scored."""

    kind: str


Parsed = Numbers | Missing | Failed | ApiError


def _last_object(text: str) -> dict[str, Any] | None:
    """The JSON object that ends last in the text, fenced or bare.

    Every "{" is tried as the start of an object. Of those that decode, the one ending last
    wins, and on a tie the one starting first, so an object nested in the answer never
    stands in for it.
    """
    decoder = json.JSONDecoder()
    best: tuple[int, int, dict[str, Any]] | None = None
    start = text.find("{")
    while start != -1:
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(value, dict) and (best is None or end > best[0]):
                best = (end, start, value)
        start = text.find("{", start + 1)
    return None if best is None else best[2]


def _number(value: Any) -> float | None:
    # bool is an int in Python, and `true` is no number of kcal.
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def parse_final_json(text: str) -> Parsed:
    """The answer in the last JSON object of a reply: three numbers, a missing list, or why not.

    Numbers may be JSON numbers or numeric strings. A key that is absent, a value that is not
    a finite number of 0 or more, or a reply with no JSON object makes a failed answer. An
    empty or null `missing` counts as no missing list, so a key left empty next to the
    numbers does not throw the numbers away.
    """
    found = _last_object(text)
    if found is None:
        return Failed("no JSON object")
    foods = found.get("missing")
    if foods is not None and foods != []:
        if not isinstance(foods, list) or not all(isinstance(f, str) for f in foods):
            return Failed("missing is not a list of food names")
        # A null or a non-number next to the list is no number given.
        given = any(_number(found.get(key)) is not None for key in ANSWER_KEYS)
        return Missing(tuple(foods), has_numbers=given)
    values: dict[str, float] = {}
    for key in ANSWER_KEYS:
        if key not in found:
            return Failed(f"no {key}")
        number = _number(found[key])
        if number is None:
            return Failed(f"{key} is not a number of 0 or more")
        values[key] = number
    return Numbers(values)


# Talking to a model and to the tools


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool as the model sees it: the server's name, description and input schema."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str | None = None


@dataclass(frozen=True, slots=True)
class UserText:
    text: str


@dataclass(frozen=True, slots=True)
class ModelTurn:
    text: str
    calls: tuple[ToolCall, ...]
    # The client's own record of the turn, sent back as it came. Gemini needs this: its
    # function calls carry thought signatures that must return unchanged.
    raw: Any = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class ToolResult:
    call: ToolCall
    # {"output": <structured result>} or {"error": <text>}, the keys Gemini documents.
    response: dict[str, Any]
    is_error: bool


@dataclass(frozen=True, slots=True)
class ToolResults:
    results: tuple[ToolResult, ...]


Message = UserText | ModelTurn | ToolResults


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class ModelReply:
    turn: ModelTurn
    usage: Usage


class ModelError(Exception):
    """A request the model API refused. `kind` names it (such as "http 400") and nothing more,
    so neither the key nor the request can reach a log or a results file."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class RetryableModelError(ModelError):
    """The API was busy (429 or 5xx) or unreachable; the same request may work later."""


class ModelClient(Protocol):
    @property
    def model(self) -> str: ...

    async def generate(
        self, system: str, history: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> ModelReply: ...


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    is_error: bool
    latency_ms: float


class ToolBox(Protocol):
    @property
    def specs(self) -> Sequence[ToolSpec]: ...

    async def call(self, call: ToolCall) -> tuple[ToolResult, ToolCallRecord]: ...


def _text(result: CallToolResult) -> str:
    return " ".join(block.text for block in result.content if isinstance(block, TextContent))


class McpToolBox:
    """The server's tools through an MCP client, each call timed on the client side."""

    def __init__(self, client: Client, specs: Sequence[ToolSpec]) -> None:
        self._client = client
        self.specs = list(specs)

    async def call(self, call: ToolCall) -> tuple[ToolResult, ToolCallRecord]:
        started = time.perf_counter()
        try:
            result = await self._client.call_tool(call.name, call.args)
        except (MCPError, ValidationError) as err:
            # A timeout or a broken connection: the model hears of it as a tool error.
            response: dict[str, Any] = {"error": f"tool call failed: {type(err).__name__}"}
            is_error = True
        else:
            is_error = bool(result.is_error)
            if is_error:
                response = {"error": _text(result) or "the tool returned an error with no text"}
            elif result.structured_content is not None:
                response = {"output": result.structured_content}
            else:
                response = {"output": {"text": _text(result)}}
        latency_ms = (time.perf_counter() - started) * 1000
        record = ToolCallRecord(call.name, dict(call.args), is_error, latency_ms)
        return ToolResult(call, response, is_error), record


@contextlib.asynccontextmanager
async def stdio_toolbox() -> AsyncIterator[ToolBox]:
    """Start the server over stdio with the bundled data only, and list its tools.

    The SDK passes the server only a few safe variables (HOME, PATH and the like) plus `env`,
    so NUTRITION_MCP_DATA_DIRS and the API key stay behind and the server's answers come from
    the same data as the golden answers.
    """
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", SERVER_MAIN],
        env={"NUTRITION_MCP_LOG_LEVEL": "WARNING"},
    )
    async with contextlib.AsyncExitStack() as stack:
        # Only starting up is wrapped, so an error raised while the arms run stays as it is.
        # A server that exits at once surfaces as an ExceptionGroup around MCPError.
        try:
            client = await stack.enter_async_context(
                Client(params, read_timeout_seconds=TOOL_TIMEOUT_S)
            )
            listed = (await client.list_tools()).tools
        except (MCPError, OSError, ExceptionGroup) as err:
            raise GroundingError(
                f"the nutrition-mcp server did not start ({type(err).__name__}); its errors "
                "are above, on stderr"
            ) from err
        specs = [ToolSpec(tool.name, tool.description or "", tool.input_schema) for tool in listed]
        yield McpToolBox(client, specs)


# Answering questions


@dataclass(slots=True)
class Outcome:
    """How one arm answered one question."""

    parsed: Parsed
    turns: int = 0
    capped: bool = False
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class Pacer:
    """Keeps at least `interval` seconds between the starts of model requests, retries included."""

    def __init__(
        self, interval: float, *, clock: Clock = time.monotonic, sleep: Sleep = anyio.sleep
    ) -> None:
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    async def wait(self) -> None:
        if self._last is not None:
            gap = self._last + self.interval - self._clock()
            if gap > 0:
                await self._sleep(gap)
        self._last = self._clock()


async def _generate(
    model: ModelClient,
    history: Sequence[Message],
    specs: Sequence[ToolSpec],
    pacer: Pacer,
    sleep: Sleep,
) -> ModelReply:
    for delay in (*RETRY_DELAYS, None):
        await pacer.wait()
        try:
            return await model.generate(SYSTEM_PROMPT, history, specs)
        except RetryableModelError as err:
            if delay is None:
                raise
            log.warning("model busy (%s), retrying in %g s", err.kind, delay)
            await sleep(delay)
    raise AssertionError("unreachable: the last delay is None")


async def answer(
    model: ModelClient,
    question: Question,
    tools: ToolBox | None,
    *,
    pacer: Pacer | None = None,
    sleep: Sleep = anyio.sleep,
) -> Outcome:
    """Ask one question, running the model's tool calls until it answers or hits MAX_TURNS.

    A request the API was too busy for or could not be reached about, after its retries, ends
    the question with an ApiError. A request the API refused (400, 403, 404 and the like) ends
    it as a failed answer that records the error kind: the model, its id or the request is at
    fault, not the quota.
    """
    pacer = pacer if pacer is not None else Pacer(0.0, sleep=sleep)
    specs = list(tools.specs) if tools is not None else []
    history: list[Message] = [UserText(question.question)]
    outcome = Outcome(Failed("turn cap reached"))
    for turn in range(1, MAX_TURNS + 1):
        try:
            reply = await _generate(model, history, specs, pacer, sleep)
        except RetryableModelError as err:
            outcome.parsed = ApiError(err.kind)
            outcome.turns = turn
            return outcome
        except ModelError as err:
            outcome.parsed = Failed("request refused", model_error=err.kind)
            outcome.turns = turn
            return outcome
        outcome.turns = turn
        outcome.input_tokens += reply.usage.input_tokens
        outcome.output_tokens += reply.usage.output_tokens
        history.append(reply.turn)
        if tools is None or not reply.turn.calls:
            outcome.parsed = parse_final_json(reply.turn.text)
            return outcome
        if turn == MAX_TURNS:
            # No turn is left to read the results, so the calls are not run.
            break
        results: list[ToolResult] = []
        for call in reply.turn.calls:
            result, record = await tools.call(call)
            results.append(result)
            outcome.tool_calls.append(record)
        history.append(ToolResults(tuple(results)))
    outcome.capped = True
    return outcome


class _ApiErrorStreak:
    """Counts questions in a row whose model request failed; at the limit, the run stops.

    Both API errors and refused requests count, so a bad key or a schema the API rejects
    stops the run as surely as a spent quota.

    It records why rather than raising: run() raises once the server has closed, because the
    MCP client's task group would wrap an error raised inside it in an ExceptionGroup.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0
        self.stopped: str | None = None

    def record(self, parsed: Parsed) -> None:
        if isinstance(parsed, ApiError):
            kind = parsed.kind
        elif isinstance(parsed, Failed) and parsed.model_error is not None:
            kind = parsed.model_error
        else:
            self.count = 0
            return
        self.count += 1
        if self.count >= self.limit:
            self.stopped = (
                f"stopped after {self.count} questions in a row whose model requests failed "
                f"(last: {kind}); check the key, the quota and the model id"
            )


def _label(parsed: Parsed) -> str:
    if isinstance(parsed, Numbers):
        return "answered"
    if isinstance(parsed, Missing):
        return "reported missing"
    if isinstance(parsed, ApiError):
        return f"API error ({parsed.kind})"
    return f"failed ({parsed.reason})"


async def run_arm(
    model: ModelClient,
    questions: Sequence[Question],
    tools: ToolBox | None,
    *,
    pacer: Pacer | None = None,
    streak: _ApiErrorStreak | None = None,
    sleep: Sleep = anyio.sleep,
) -> dict[str, Outcome]:
    arm = "without_tools" if tools is None else "with_tools"
    streak = streak if streak is not None else _ApiErrorStreak(MAX_CONSECUTIVE_API_ERRORS)
    outcomes: dict[str, Outcome] = {}
    for question in questions:
        outcome = await answer(model, question, tools, pacer=pacer, sleep=sleep)
        # Ids and outcomes only: questions and answers stay out of the logs.
        log.info("%s %s: %s", arm, question.id, _label(outcome.parsed))
        outcomes[question.id] = outcome
        streak.record(outcome.parsed)
        if streak.stopped:
            break
    return outcomes


# Scoring


def ape(golden: float, value: float) -> float:
    """Absolute percentage error of `value` against a golden value above 0."""
    return abs(value - golden) / golden * 100


def reports_missing(question: Question, parsed: Parsed) -> bool:
    """Whether the answer is a missing list, with no numbers, naming every expected dish.

    An entry names a dish when, trimmed and ignoring case, either one contains the other: "veg
    pulao" reports "pulao", and "chicken" reports "chicken curry". An empty entry names none.
    """
    if not isinstance(parsed, Missing) or parsed.has_numbers:
        return False
    named = [entry for entry in (food.strip().lower() for food in parsed.foods) if entry]
    return all(
        any(dish in entry or entry in dish for entry in named)
        for dish in (name.strip().lower() for name in question.expect_missing)
    )


def _share(count: int, total: int) -> float | None:
    return round(count / total, 2) if total else None


def paired_ids(
    questions: Sequence[Question], without: Mapping[str, Outcome], with_: Mapping[str, Outcome]
) -> list[str]:
    """The questions with items that both arms answered with numbers, in question order."""
    return [
        q.id
        for q in questions
        if q.items
        and isinstance(without[q.id].parsed, Numbers)
        and isinstance(with_[q.id].parsed, Numbers)
    ]


def score_arm(
    questions: Sequence[Question],
    golden: Mapping[str, Golden],
    outcomes: Mapping[str, Outcome],
    paired: Collection[str],
) -> dict[str, Any]:
    """MAPE and within-10% shares per nutrient, failed answers and missing dishes reported.

    MAPE covers only the `paired` questions, the ones both arms answered with numbers, so the
    two arms' errors are means over the same questions and a question one arm skipped cannot
    move one mean and not the other. The within-10% shares cover every question with items
    that this arm got a reply to (`n_items`): an answer without numbers, failed or a missing
    list, counts as outside 10%, and a request the API refused is such a failed answer. A
    question with an API error (busy or unreachable after the retries) got no answer at all,
    so it is left out of every measure and only counted in `api_errors`.
    """
    answered = [q for q in questions if not isinstance(outcomes[q.id].parsed, ApiError)]
    with_items = [q for q in answered if q.items]
    expecting_missing = [q for q in answered if q.expect_missing]
    errors: dict[str, dict[str, float]] = {key: {} for key in ANSWER_KEYS}
    for question in with_items:
        parsed = outcomes[question.id].parsed
        if isinstance(parsed, Numbers):
            for key in ANSWER_KEYS:
                errors[key][question.id] = ape(golden[question.id][key], parsed.values[key])
    metrics: dict[str, Any] = {
        # Questions with items this arm answered with numbers, paired or not.
        "n_numbers": len(errors["kcal"]),
        # The denominators of the shares and counts below, which leave out API errors.
        "n_items": len(with_items),
        "n_missing_dishes": len(expecting_missing),
        "n_replied": len(answered),
    }
    for key, name in METRIC_NAMES.items():
        found = [errors[key][qid] for qid in paired]
        metrics[f"mape_{name}"] = round(sum(found) / len(found), 1) if found else None
    for key, name in METRIC_NAMES.items():
        # Rounded first, so float noise cannot push an error of exactly 10% outside.
        within = sum(round(error, 6) <= WITHIN_PCT for error in errors[key].values())
        metrics[f"within_10pct_{name}"] = _share(within, len(with_items))
    metrics["failed_answers"] = sum(isinstance(outcomes[q.id].parsed, Failed) for q in questions)
    metrics["missing_answers"] = sum(isinstance(outcomes[q.id].parsed, Missing) for q in with_items)
    reported = sum(reports_missing(q, outcomes[q.id].parsed) for q in expecting_missing)
    metrics["missing_reported"] = _share(reported, len(expecting_missing))
    metrics["api_errors"] = len(questions) - len(answered)
    return metrics


def p95(values: Sequence[float]) -> float | None:
    """The 95th percentile by nearest rank: the value at rank ceil(0.95 n) in sorted order."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


# The results file


def _arm_row(outcome: Outcome, golden: Golden | None) -> dict[str, Any]:
    parsed = outcome.parsed
    row: dict[str, Any] = {
        "answer": parsed.values if isinstance(parsed, Numbers) else None,
        "missing": list(parsed.foods) if isinstance(parsed, Missing) else None,
        "failure": parsed.reason if isinstance(parsed, Failed) else None,
        "model_error": parsed.model_error if isinstance(parsed, Failed) else None,
        "api_error": parsed.kind if isinstance(parsed, ApiError) else None,
        "ape": None,
        "turns": outcome.turns,
        "turns_capped": outcome.capped,
        "tool_calls": [
            {
                "name": record.name,
                "arguments": record.arguments,
                "is_error": record.is_error,
                "latency_ms": round(record.latency_ms, 2),
            }
            for record in outcome.tool_calls
        ],
    }
    if golden is not None and isinstance(parsed, Numbers):
        row["ape"] = {key: round(ape(golden[key], parsed.values[key]), 1) for key in ANSWER_KEYS}
    return row


def _tokens(outcomes: Mapping[str, Outcome]) -> dict[str, int]:
    return {
        "input": sum(outcome.input_tokens for outcome in outcomes.values()),
        "output": sum(outcome.output_tokens for outcome in outcomes.values()),
    }


def summary_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The rows summary.md shows, headline first, each value copied from the arms."""
    without, with_ = result["without_tools"], result["with_tools"]

    def row(measure: str, n: str | None, metric: str) -> dict[str, Any]:
        # Each arm's own n: answers fail and requests error per arm, so the two can differ.
        return {
            "measure": measure,
            "n_without_tools": without[n] if n else result["n"],
            "n_with_tools": with_[n] if n else result["n"],
            "without_tools": without[metric],
            "with_tools": with_[metric],
        }

    def paired(measure: str, metric: str) -> dict[str, Any]:
        # Both arms' errors are over the same questions, so they share one n.
        return {**row(measure, None, metric), **dict.fromkeys(N_KEYS, result["n_paired"])}

    def tools_only(measure: str, n: Any, value: Any) -> dict[str, Any]:
        # Measures with no baseline: the without-tools arm has no tools and no data.
        return {
            "measure": measure,
            "n_without_tools": None,
            "n_with_tools": n,
            "without_tools": None,
            "with_tools": value,
        }

    return [
        # The golden answers are the server's own totals, so the error is against those, not
        # against what the food contains; the data-accuracy suite measures that.
        paired("Calorie error against the dataset totals, MAPE (%)", "mape_kcal"),
        paired("Protein error against the dataset totals, MAPE (%)", "mape_protein"),
        paired("Carbohydrate error against the dataset totals, MAPE (%)", "mape_carbs"),
        row("Answers within 10% on calories (share)", "n_items", "within_10pct_kcal"),
        # Without tools the model has no data to look a dish up in, so a missing list from it
        # says nothing about the data.
        tools_only(
            "Missing dishes reported (share)",
            with_["n_missing_dishes"],
            with_["missing_reported"],
        ),
        row("Failed answers (count)", "n_replied", "failed_answers"),
        row("API errors (count)", None, "api_errors"),
        tools_only("Tool errors (count)", result["tool_calls"], result["tool_errors"]["count"]),
        tools_only("p95 tool latency (ms)", result["tool_calls"], result["p95_tool_latency_ms"]),
    ]


def build_result(
    model: str,
    questions: Sequence[Question],
    golden: Mapping[str, Golden],
    without: Mapping[str, Outcome],
    with_: Mapping[str, Outcome],
    *,
    now: datetime,
    commit: str,
    dataset_version: str,
    min_interval: float,
) -> dict[str, Any]:
    utc = now.astimezone(UTC)
    records = [record for outcome in with_.values() for record in outcome.tool_calls]
    errors = Counter(
        record.name if record.name in TOOL_NAMES else "unknown"
        for record in records
        if record.is_error
    )
    latency = p95([record.latency_ms for record in records])
    paired = paired_ids(questions, without, with_)
    result: dict[str, Any] = {
        "suite": "grounding",
        "date": utc.date().isoformat(),
        "generated_at": utc.isoformat(timespec="seconds"),
        "commit": commit,
        "dataset_version": dataset_version,
        "model": model,
        "tier": "free",
        "cost_usd": 0.0,
        "temperature": TEMPERATURE,
        "max_turns": MAX_TURNS,
        "min_request_interval_s": min_interval,
        "n": len(questions),
        "questions_with_items": sum(bool(q.items) for q in questions),
        "questions_expecting_missing": sum(bool(q.expect_missing) for q in questions),
        "tokens": {"without_tools": _tokens(without), "with_tools": _tokens(with_)},
        # Questions with items both arms answered with numbers: the n of every MAPE.
        "n_paired": len(paired),
        "without_tools": score_arm(questions, golden, without, paired),
        "with_tools": score_arm(questions, golden, with_, paired),
        "tool_calls": len(records),
        "tool_errors": {"count": sum(errors.values()), "by_tool": dict(sorted(errors.items()))},
        "p95_tool_latency_ms": None if latency is None else round(latency, 1),
        "turns_capped": sum(outcome.capped for outcome in with_.values()),
    }
    result["summary"] = summary_rows(result)
    result["rows"] = [
        {
            "id": question.id,
            "question": question.question,
            "golden": golden.get(question.id),
            "expect_missing": list(question.expect_missing),
            "without_tools": _arm_row(without[question.id], golden.get(question.id)),
            "with_tools": _arm_row(with_[question.id], golden.get(question.id)),
        }
        for question in questions
    ]
    return result


Toolboxes = Callable[[], contextlib.AbstractAsyncContextManager[ToolBox]]


def _require_numbers(
    arm: str, questions: Sequence[Question], outcomes: Mapping[str, Outcome]
) -> None:
    """Raise GroundingError when the arm answered no question with items with numbers.

    Such a run has no error to report for that arm: it measures the key, the service or a
    model that never answers, and its results must never replace a real headline.
    """
    with_items = [q for q in questions if q.items]
    kinds = Counter(type(outcomes[q.id].parsed).__name__ for q in with_items)
    if not kinds["Numbers"]:
        raise GroundingError(
            f"the {arm} arm gave no answer with numbers to any of the {len(with_items)} "
            f"questions with items (API errors {kinds['ApiError']}, failed answers "
            f"{kinds['Failed']}, missing lists {kinds['Missing']})"
        )


async def run(
    model: ModelClient,
    questions: Sequence[Question],
    *,
    now: datetime,
    commit: str,
    dataset_version: str,
    min_interval: float,
    toolbox: Toolboxes = stdio_toolbox,
    clock: Clock = time.monotonic,
    sleep: Sleep = anyio.sleep,
) -> dict[str, Any]:
    """Both arms over the questions, as a results dict for run_evals.write_results.

    Golden answers are worked out and the server is started before the first model request,
    so a bad question or a server that will not start costs no requests. Every request, in
    both arms, waits on one pacer, `min_interval` seconds apart.
    """
    golden = golden_answers(questions)
    pacer = Pacer(min_interval, clock=clock, sleep=sleep)
    # One streak across both arms: errors at the end of one and the start of the other are
    # still in a row.
    streak = _ApiErrorStreak(MAX_CONSECUTIVE_API_ERRORS)
    with_: dict[str, Outcome] = {}
    async with toolbox() as tools:
        without = await run_arm(model, questions, None, pacer=pacer, streak=streak, sleep=sleep)
        if not streak.stopped:
            with_ = await run_arm(model, questions, tools, pacer=pacer, streak=streak, sleep=sleep)
    if streak.stopped:
        raise GroundingError(streak.stopped)
    for arm, outcomes in (("without_tools", without), ("with_tools", with_)):
        _require_numbers(arm, questions, outcomes)
    return build_result(
        model.model,
        questions,
        golden,
        without,
        with_,
        now=now,
        commit=commit,
        dataset_version=dataset_version,
        min_interval=min_interval,
    )


# Gemini through google-genai. The import waits until a client is built, because CI's
# data-accuracy job installs no eval group and still imports run_evals.

# The JSON Schema keywords kept in a function declaration: the subset Gemini documents for
# schemas. Others, such as exclusiveMinimum, are dropped; the descriptions still state them.
_SCHEMA_KEYWORDS = frozenset(
    {
        "$defs",
        "$ref",
        "additionalProperties",
        "anyOf",
        "default",
        "description",
        "enum",
        "format",
        "items",
        "maxItems",
        "maximum",
        "minItems",
        "minimum",
        "properties",
        "required",
        "title",
        "type",
    }
)
_SCHEMA_MAPS = frozenset({"properties", "$defs"})


def gemini_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of a JSON Schema with only the keywords Gemini documents, at every depth."""
    kept: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SCHEMA_KEYWORDS:
            continue
        if key in _SCHEMA_MAPS and isinstance(value, Mapping):
            kept[key] = {name: gemini_schema(inner) for name, inner in value.items()}
        elif key == "anyOf" and isinstance(value, list):
            kept[key] = [gemini_schema(inner) for inner in value]
        elif key in {"items", "additionalProperties"} and isinstance(value, Mapping):
            kept[key] = gemini_schema(value)
        else:
            kept[key] = value
    return kept


def function_declaration(spec: ToolSpec) -> "types.FunctionDeclaration":
    from google.genai import types

    return types.FunctionDeclaration(
        name=spec.name,
        description=spec.description,
        parameters_json_schema=gemini_schema(spec.parameters),
    )


def to_contents(history: Sequence[Message]) -> list["types.Content"]:
    """The conversation as google-genai contents. Tool results go back in a user turn."""
    from google.genai import types

    contents: list[types.Content] = []
    for message in history:
        if isinstance(message, UserText):
            contents.append(types.Content(role="user", parts=[types.Part(text=message.text)]))
        elif isinstance(message, ModelTurn):
            if isinstance(message.raw, types.Content):
                contents.append(message.raw)
                continue
            parts = [types.Part(text=message.text)] if message.text else []
            parts += [
                types.Part(
                    function_call=types.FunctionCall(id=call.id, name=call.name, args=call.args)
                )
                for call in message.calls
            ]
            contents.append(types.Content(role="model", parts=parts))
        else:
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=result.call.id, name=result.call.name, response=result.response
                            )
                        )
                        for result in message.results
                    ],
                )
            )
    return contents


def reply_from_response(response: "types.GenerateContentResponse") -> ModelReply:
    """The first candidate's text and function calls, with token counts.

    Thought parts are left out of the text. Thinking tokens count as output, as they are billed.
    """
    content = response.candidates[0].content if response.candidates else None
    parts = (content.parts or []) if content is not None else []
    text = "".join(part.text for part in parts if part.text and not part.thought)
    calls = tuple(
        ToolCall(
            part.function_call.name or "",
            dict(part.function_call.args or {}),
            part.function_call.id,
        )
        for part in parts
        if part.function_call is not None
    )
    usage = response.usage_metadata
    tokens = Usage(
        input_tokens=(usage.prompt_token_count or 0) if usage else 0,
        output_tokens=((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0))
        if usage
        else 0,
    )
    return ModelReply(ModelTurn(text, calls, content), tokens)


def model_error(err: "genai_errors.APIError") -> ModelError:
    """The status code alone: the error's text can quote the request."""
    kind = f"http {err.code}"
    # The codes google-genai itself retries when asked to: timeout, too many requests, 5xx.
    if err.code in (408, 429) or err.code >= 500:
        return RetryableModelError(kind)
    return ModelError(kind)


class GeminiClient:
    """ModelClient over google-genai's async API, with automatic function calling off.

    Temperature is not set, so the model runs at the provider's default (see TEMPERATURE).
    """

    def __init__(self, model: str, client: "genai.Client") -> None:
        self._model = model
        self._client = client

    @property
    def model(self) -> str:
        return self._model

    async def generate(
        self, system: str, history: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> ModelReply:
        import httpx
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[function_declaration(t) for t in tools])]
            if tools
            else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model, contents=to_contents(history), config=config
            )
        except errors.APIError as err:
            raise model_error(err) from None
        except httpx.TransportError as err:
            raise RetryableModelError(f"network {type(err).__name__}") from None
        return reply_from_response(response)


def gemini_client(model: str, api_key: str) -> GeminiClient:
    from google import genai
    from google.genai import types

    options = types.HttpOptions(timeout=REQUEST_TIMEOUT_MS)
    return GeminiClient(model, genai.Client(api_key=api_key, http_options=options))
