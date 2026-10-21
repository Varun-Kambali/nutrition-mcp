import contextlib
import functools
import itertools
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import grounding
import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types
from mcp import MCPError

from nutrition_mcp.config import Settings
from nutrition_mcp.loader import build_registry
from nutrition_mcp.queries import meal_totals
from nutrition_mcp.schemas import MealItemIn
from nutrition_mcp.search import search_foods

# Questions file


@pytest.fixture(scope="module")
def questions() -> list[grounding.Question]:
    return grounding.load_questions(grounding.QUESTIONS)


def test_questions_file_rows_are_valid(questions: list[grounding.Question]) -> None:
    assert 26 <= len(questions) <= 34
    assert len({q.id for q in questions}) == len(questions)
    for q in questions:
        # A question names food the way a person would, never by its id.
        for item in q.items:
            if "_" in item.food_id:
                assert item.food_id not in q.question, q.id
    with_items = [q for q in questions if q.items]
    assert {len(q.items) for q in with_items} >= {1, 2, 3, 4}
    units = {item.unit for q in with_items for item in q.items}
    assert {"katori", "piece", "roti", "glass", "g"} <= units
    assert sum("carb" in q.question.lower() for q in with_items) >= 4


def test_questions_cover_every_region(questions: list[grounding.Question]) -> None:
    # Only dishes tagged with one region count: steamed rice alone carries all four.
    reg = build_registry([])
    per_region: dict[str, set[str]] = {}
    for q in questions:
        for item in q.items:
            regions = reg.get(item.food_id).regions
            if len(regions) == 1:
                per_region.setdefault(regions[0], set()).add(q.id)
    assert set(per_region) == {"north", "south", "east", "west"}
    assert all(len(ids) >= 4 for ids in per_region.values()), per_region


def test_questions_resolve_to_complete_golden_answers(
    questions: list[grounding.Question],
) -> None:
    golden = grounding.golden_answers(questions)
    with_items = [q for q in questions if q.items]
    assert set(golden) == {q.id for q in with_items}
    for answer in golden.values():
        # Every golden value is above 0, so each one has a percentage error.
        assert all(answer[key] > 0 for key in grounding.ANSWER_KEYS)


def test_missing_dishes_are_not_in_the_data(questions: list[grounding.Question]) -> None:
    missing = [q for q in questions if q.expect_missing]
    assert {name for q in missing for name in q.expect_missing} == {
        "chicken curry",
        "masala dosa",
        "pulao",
        "kheer",
    }
    reg = build_registry([])
    for q in missing:
        assert not q.items
        for name in q.expect_missing:
            assert name in q.question.lower()
            assert search_foods(reg, Settings(), name, None, None, None).results == []


def test_golden_from_items() -> None:
    question = grounding.Question(
        id="t1",
        question="2 rotis and a katori of rajma",
        items=(
            MealItemIn(food_id="chapati", amount=2, unit="roti"),
            MealItemIn(food_id="rajma_masala", amount=1, unit="katori"),
        ),
    )
    golden = grounding.golden_answers([question])["t1"]

    total = meal_totals(build_registry([]), Settings(), list(question.items)).total
    assert golden == {
        "kcal": total.kcal,
        "protein_g": total.protein_g,
        "carbs_g": total.carbs_available_g,
    }
    # 2 rotis of 40 g each: 80 g of chapati at 223.125 kcal per 100 g, plus the rajma.
    assert golden["kcal"] > 178.5


def test_golden_answers_use_the_default_katori_whatever_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The server the with-tools arm starts gets none of the caller's NUTRITION_MCP_* settings,
    # so the golden side must not pick them up either.
    question = grounding.Question(
        id="t1",
        question="a katori of rajma",
        items=(MealItemIn(food_id="rajma_masala", amount=1, unit="katori"),),
    )
    before = grounding.golden_answers([question])
    monkeypatch.setenv("NUTRITION_MCP_DEFAULT_KATORI_G", "300")
    assert grounding.golden_answers([question]) == before


def test_an_incomplete_golden_answer_is_an_error() -> None:
    # misal_pav's available carbohydrate is incomplete: one ingredient has no value.
    question = grounding.Question(
        id="t1",
        question="a plate of misal pav",
        items=(MealItemIn(food_id="misal_pav", amount=1, unit="plate"),),
    )
    with pytest.raises(grounding.GroundingError, match="t1"):
        grounding.golden_answers([question])


def test_an_unknown_food_in_a_question_is_an_error() -> None:
    question = grounding.Question(
        id="t1",
        question="a katori of x",
        items=(MealItemIn(food_id="no_such_food", amount=1, unit="g"),),
    )
    with pytest.raises(grounding.GroundingError, match="t1"):
        grounding.golden_answers([question])


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("not json", "line 1"),
        ('{"id": "a", "question": "q", "items": []}', "line 1"),
        (
            '{"id": "a", "question": "q", "items": [], "expect_missing": ["x"], "extra": 1}',
            "line 1",
        ),
        (
            '{"id": "a", "question": "q", "items": [{"food_id": "idli", "amount": 1, '
            '"unit": "piece"}], "expect_missing": ["x"]}',
            "line 1",
        ),
    ],
)
def test_a_bad_question_row_names_its_line(tmp_path: Path, line: str, problem: str) -> None:
    path = tmp_path / "questions.jsonl"
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(grounding.GroundingError, match=problem):
        grounding.load_questions(path)


def test_question_ids_must_be_unique(tmp_path: Path) -> None:
    row = {"id": "a", "question": "q", "items": [], "expect_missing": ["x"]}
    path = tmp_path / "questions.jsonl"
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(grounding.GroundingError, match="line 2"):
        grounding.load_questions(path)


def test_a_golden_value_of_zero_is_an_error() -> None:
    # Salt has no energy, protein or carbohydrate, and 0 has no percentage error.
    question = grounding.Question(
        id="t1", question="5 g of salt", items=(MealItemIn(food_id="salt", amount=5, unit="g"),)
    )
    with pytest.raises(grounding.GroundingError, match="t1"):
        grounding.golden_answers([question])


# Parsing the final answer


def numbers(kcal: float, protein_g: float, carbs_g: float) -> grounding.Numbers:
    return grounding.Numbers({"kcal": kcal, "protein_g": protein_g, "carbs_g": carbs_g})


def test_parse_final_json() -> None:
    text = 'Adding it up gives this.\n{"kcal": 350, "protein_g": 12, "carbs_g": 50}\nEnjoy.'
    assert grounding.parse_final_json(text) == numbers(350, 12, 50)

    # Missing keys are a failed answer, not a crash.
    failed = grounding.parse_final_json('{"kcal": 350, "protein_g": 12}')
    assert isinstance(failed, grounding.Failed)
    assert "carbs_g" in failed.reason


def test_parse_takes_the_last_object_fenced_or_bare() -> None:
    text = (
        'First guess {"kcal": 1, "protein_g": 1, "carbs_g": 1}, then the tools:\n'
        '```json\n{"kcal": 410.5, "protein_g": 9.25, "carbs_g": 62}\n```'
    )
    assert grounding.parse_final_json(text) == numbers(410.5, 9.25, 62)


@pytest.mark.parametrize("missing", ["[]", "null"])
def test_an_empty_missing_list_next_to_numbers_is_no_missing_list(missing: str) -> None:
    text = f'{{"kcal": 350, "protein_g": 12, "carbs_g": 50, "missing": {missing}}}'
    assert grounding.parse_final_json(text) == numbers(350, 12, 50)
    # With no numbers either, there is no answer.
    assert isinstance(grounding.parse_final_json(f'{{"missing": {missing}}}'), grounding.Failed)


def test_parse_accepts_numeric_strings() -> None:
    text = '{"kcal": "350", "protein_g": " 12.5 ", "carbs_g": 50.0}'
    assert grounding.parse_final_json(text) == numbers(350, 12.5, 50)


@pytest.mark.parametrize(
    "text",
    [
        "About 350 kcal and 12 g of protein.",
        '{"kcal": "lots", "protein_g": 12, "carbs_g": 50}',
        '{"kcal": true, "protein_g": 12, "carbs_g": 50}',
        '{"kcal": null, "protein_g": 12, "carbs_g": 50}',
        '{"kcal": -5, "protein_g": 12, "carbs_g": 50}',
        '{"kcal": NaN, "protein_g": 12, "carbs_g": 50}',
        '{"kcal": "inf", "protein_g": 12, "carbs_g": 50}',
        "{kcal: 350, protein_g: 12, carbs_g: 50}",
        '{"missing": "kheer"}',
        '{"missing": []}',
        "",
    ],
)
def test_parse_fails_without_a_usable_answer(text: str) -> None:
    assert isinstance(grounding.parse_final_json(text), grounding.Failed)


def test_parse_reads_a_missing_list() -> None:
    assert grounding.parse_final_json(
        'Not in the data.\n{"missing": ["chicken curry"]}'
    ) == grounding.Missing(("chicken curry",), has_numbers=False)
    assert grounding.parse_final_json('{"missing": ["kheer"], "kcal": 200}') == grounding.Missing(
        ("kheer",), has_numbers=True
    )
    # A null or a non-number is no number given.
    assert grounding.parse_final_json(
        '{"missing": ["kheer"], "kcal": null, "protein_g": "n/a"}'
    ) == grounding.Missing(("kheer",), has_numbers=False)


# Provenance


def test_questions_version_follows_the_questions_and_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "questions.jsonl"
    path.write_text('{"id": "a"}\n', encoding="utf-8")
    version = grounding.questions_version(path)
    assert len(version) == 12 and set(version) <= set("0123456789abcdef")
    assert grounding.questions_version(path) == version

    path.write_text('{"id": "b"}\n', encoding="utf-8")
    edited = grounding.questions_version(path)
    assert edited != version
    monkeypatch.setattr(grounding, "SYSTEM_PROMPT", grounding.SYSTEM_PROMPT + " Be brief.")
    assert grounding.questions_version(path) != edited


def test_a_failed_row_records_the_finish_reason() -> None:
    def script(history: Sequence[grounding.Message], tools: Sequence[grounding.ToolSpec]) -> Any:
        text = '{"kcal": 150, "protein_g": 4, "carbs_g": 30}'
        if not tools and history[0].text == "first":
            # Cut off before the JSON answer.
            text = "The idlis come to roughly"
            return grounding.ModelReply(
                grounding.ModelTurn(text, ()), grounding.Usage(1, 1), "MAX_TOKENS"
            )
        return grounding.ModelReply(grounding.ModelTurn(text, ()), grounding.Usage(1, 1), "STOP")

    @contextlib.asynccontextmanager
    async def toolbox() -> Any:
        yield FakeTools()

    asked = [
        grounding.Question(id="q1", question="first", items=question("q1").items),
        question("q2"),
    ]
    result = run(
        grounding.run,
        FakeModel(script),
        asked,
        now=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        commit="abc1234",
        dataset_version="0123456789ab",
        dirty=True,
        questions_version="fedcba987654",
        min_interval=0.0,
        toolbox=toolbox,
    )
    rows = {row["id"]: row for row in result["rows"]}
    assert rows["q1"]["without_tools"]["failure"] == "no JSON object"
    assert rows["q1"]["without_tools"]["finish_reason"] == "MAX_TOKENS"
    # Answers that were read have no finish reason to explain.
    assert rows["q1"]["with_tools"]["finish_reason"] is None
    assert rows["q2"]["without_tools"]["finish_reason"] is None
    assert result["dirty"] is True


# Scoring


def question(qid: str, *, missing: tuple[str, ...] = ()) -> grounding.Question:
    if missing:
        return grounding.Question(
            id=qid, question=f"a katori of {missing[0]}", expect_missing=missing
        )
    return grounding.Question(
        id=qid, question="2 idlis", items=(MealItemIn(food_id="idli", amount=2, unit="piece"),)
    )


@pytest.mark.parametrize(
    ("parsed", "reported"),
    [
        (grounding.Missing(("Chicken Curry (home style)",), has_numbers=False), True),
        (grounding.Missing(("rice", "chicken curry"), has_numbers=False), True),
        (grounding.Missing(("chicken curry recipe",), has_numbers=False), True),
        # The entry may also be part of the dish's name.
        (grounding.Missing(("chicken",), has_numbers=False), True),
        (grounding.Missing(("  CHICKEN ",), has_numbers=False), True),
        (grounding.Missing(("rice",), has_numbers=False), False),
        (grounding.Missing(("", "  "), has_numbers=False), False),
        (grounding.Missing(("chicken curry",), has_numbers=True), False),
        (grounding.Numbers({"kcal": 300.0, "protein_g": 20.0, "carbs_g": 10.0}), False),
        (grounding.Failed("no JSON object"), False),
    ],
)
def test_missing_reported_scoring(parsed: grounding.Parsed, reported: bool) -> None:
    asked = question("m1", missing=("chicken curry",))
    assert grounding.reports_missing(asked, parsed) is reported


def test_metrics() -> None:
    questions = [question(f"q{i}") for i in range(1, 5)] + [
        question("m1", missing=("kheer",)),
        question("m2", missing=("pulao",)),
    ]
    golden = {f"q{i}": {"kcal": 200.0, "protein_g": 10.0, "carbs_g": 40.0} for i in range(1, 5)}
    outcomes = {
        # Exact.
        "q1": grounding.Outcome(numbers(200, 10, 40)),
        # kcal 10% over (still within), protein 50% over, carbs 25% under.
        "q2": grounding.Outcome(numbers(220, 15, 30)),
        # No usable answer: outside 10% and left out of the MAPE.
        "q3": grounding.Outcome(grounding.Failed("no JSON object")),
        # A known food reported missing: also outside 10% and left out of the MAPE.
        "q4": grounding.Outcome(grounding.Missing(("idli",), has_numbers=False)),
        "m1": grounding.Outcome(grounding.Missing(("kheer",), has_numbers=False)),
        "m2": grounding.Outcome(numbers(300, 5, 50)),
    }

    metrics = grounding.score_arm(questions, golden, outcomes, paired=["q1", "q2"])

    assert metrics == {
        "n_numbers": 2,
        "n_items": 4,
        "n_missing_dishes": 2,
        "n_replied": 6,
        "mape_kcal": 5.0,
        "mape_protein": 25.0,
        "mape_carbs": 12.5,
        "within_10pct_kcal": 0.5,
        "within_10pct_protein": 0.25,
        "within_10pct_carbs": 0.25,
        "failed_answers": 1,
        "missing_answers": 1,
        "missing_reported": 0.5,
        "api_errors": 0,
    }


def test_metrics_with_no_scored_answer_have_no_mape() -> None:
    questions = [question("q1")]
    golden = {"q1": {"kcal": 200.0, "protein_g": 10.0, "carbs_g": 40.0}}
    metrics = grounding.score_arm(
        questions, golden, {"q1": grounding.Outcome(grounding.Failed("x"))}, paired=[]
    )
    assert metrics["n_numbers"] == 0
    assert metrics["mape_kcal"] is None
    assert metrics["within_10pct_kcal"] == 0.0
    assert metrics["missing_reported"] is None


def test_mape_covers_the_questions_both_arms_answered_with_numbers() -> None:
    questions = [question("q1"), question("q2"), question("q3")]
    golden = {q: {"kcal": 200.0, "protein_g": 10.0, "carbs_g": 40.0} for q in ("q1", "q2", "q3")}
    without = {
        "q1": grounding.Outcome(numbers(200, 10, 40)),
        "q2": grounding.Outcome(numbers(300, 15, 60)),
        "q3": grounding.Outcome(grounding.Failed("no JSON object")),
    }
    with_ = {
        "q1": grounding.Outcome(numbers(220, 11, 44)),
        "q2": grounding.Outcome(grounding.Missing(("idli",), has_numbers=False)),
        "q3": grounding.Outcome(numbers(200, 10, 40)),
    }
    result = grounding.build_result(
        "fake-model",
        questions,
        golden,
        without,
        with_,
        now=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        commit="abc1234",
        dataset_version="0123456789ab",
        dirty=False,
        questions_version="fedcba987654",
        min_interval=0.0,
    )

    # Only q1 has numbers from both arms, so each MAPE is over q1 alone.
    assert result["n_paired"] == 1
    assert result["without_tools"]["mape_kcal"] == 0.0
    assert result["with_tools"]["mape_kcal"] == 10.0
    # Within 10% keeps each arm's own denominator: q3 failed without tools, q2 with them.
    assert result["without_tools"]["within_10pct_kcal"] == round(1 / 3, 2)
    assert result["with_tools"]["within_10pct_kcal"] == round(2 / 3, 2)
    rows = {row["measure"]: row for row in result["summary"]}
    # The headline, calorie MAPE, comes first.
    mape = result["summary"][0]
    assert (mape["n_without_tools"], mape["n_with_tools"]) == (1, 1)
    within = rows["Answers within 10% on calories (share)"]
    assert (within["n_without_tools"], within["n_with_tools"]) == (3, 3)


def test_p95_is_the_nearest_rank() -> None:
    assert grounding.p95([float(v) for v in range(20, 0, -1)]) == 19.0
    assert grounding.p95([float(v) for v in range(1, 101)]) == 95.0
    assert grounding.p95([7.5]) == 7.5
    assert grounding.p95([]) is None


# Arms with a fake model


class FakeModel:
    """Answers from a script: a function of the history and the tools offered."""

    model = "fake-model"

    def __init__(self, script: Any) -> None:
        self.script = script
        self.requests: list[tuple[str, list[grounding.Message], list[grounding.ToolSpec]]] = []

    async def generate(
        self,
        system: str,
        history: Sequence[grounding.Message],
        tools: Sequence[grounding.ToolSpec],
    ) -> grounding.ModelReply:
        self.requests.append((system, list(history), list(tools)))
        reply = self.script(history, tools)
        if isinstance(reply, Exception):
            raise reply
        return reply


def reply(text: str = "", *calls: grounding.ToolCall) -> grounding.ModelReply:
    return grounding.ModelReply(grounding.ModelTurn(text, calls), grounding.Usage(100, 20))


class FakeTools:
    """A toolbox that answers every call with the same output and records it."""

    def __init__(self, *, error: bool = False, instructions: str | None = None) -> None:
        self.specs = [grounding.ToolSpec("search_food", "Find foods.", {"type": "object"})]
        self.instructions = instructions
        self.error = error
        self.calls: list[grounding.ToolCall] = []

    async def call(
        self, call: grounding.ToolCall
    ) -> tuple[grounding.ToolResult, grounding.ToolCallRecord]:
        self.calls.append(call)
        response = {"error": "unknown food id"} if self.error else {"output": {"results": []}}
        return (
            grounding.ToolResult(call, response, self.error),
            grounding.ToolCallRecord(call.name, dict(call.args), self.error, 1.5),
        )


def run(coro_fn: Any, *args: Any, **kwargs: Any) -> Any:
    return anyio.run(functools.partial(coro_fn, *args, **kwargs))


def test_the_without_tools_arm_asks_each_question_once() -> None:
    answers = {
        "2 idlis": '{"kcal": 150, "protein_g": 4, "carbs_g": 30}',
        "a katori of kheer": 'Roughly this.\n{"kcal": 250, "protein_g": 6, "carbs_g": 40}',
    }
    model = FakeModel(lambda history, tools: reply(answers[history[-1].text]))
    questions = [question("q1"), question("m1", missing=("kheer",))]

    outcomes = run(grounding.run_arm, model, questions, None)

    assert outcomes["q1"].parsed == numbers(150, 4, 30)
    assert outcomes["m1"].parsed == numbers(250, 6, 40)
    assert (outcomes["q1"].turns, outcomes["q1"].input_tokens, outcomes["q1"].output_tokens) == (
        1,
        100,
        20,
    )
    assert len(model.requests) == 2
    for system, history, tools in model.requests:
        assert system == grounding.SYSTEM_PROMPT
        assert tools == []
        assert len(history) == 1


def test_the_prompt_asks_for_the_answer_shape_and_defines_carbs() -> None:
    prompt = grounding.SYSTEM_PROMPT
    assert '{"kcal": <number>, "protein_g": <number>, "carbs_g": <number>}' in prompt
    assert '{"missing": ["<food as named in the question>"]}' in prompt
    assert "minus" in prompt and "fibre" in prompt


def test_the_with_tools_arm_sends_tool_results_back() -> None:
    def script(history: Sequence[grounding.Message], tools: Sequence[grounding.ToolSpec]) -> Any:
        if len(history) == 1:
            return reply("", grounding.ToolCall("search_food", {"query": "idli"}, "c1"))
        assert isinstance(history[-1], grounding.ToolResults)
        assert history[-1].results[0].response == {"output": {"results": []}}
        return reply('{"kcal": 145, "protein_g": 4, "carbs_g": 29}')

    model = FakeModel(script)
    tools = FakeTools()
    outcome = run(grounding.answer, model, question("q1"), tools)

    assert outcome.parsed == numbers(145, 4, 29)
    assert outcome.turns == 2 and not outcome.capped
    assert [record.name for record in outcome.tool_calls] == ["search_food"]
    assert model.requests[0][2] == tools.specs
    assert (outcome.input_tokens, outcome.output_tokens) == (200, 40)


def test_the_with_tools_arm_adds_the_server_instructions_to_the_prompt() -> None:
    model = FakeModel(lambda history, tools: reply('{"kcal": 145, "protein_g": 4, "carbs_g": 29}'))
    tools = FakeTools(instructions="Call search_food first.")
    run(grounding.answer, model, question("q1"), tools)
    [(system, _, _)] = model.requests
    # The task prompt comes first, unchanged, then what the server sent at connection.
    assert system.startswith(grounding.SYSTEM_PROMPT)
    assert system.endswith(
        "\n\nInstructions from the nutrition-mcp server:\nCall search_food first."
    )


@pytest.mark.parametrize("instructions", [None, ""])
def test_a_server_without_instructions_leaves_the_prompt_as_it_is(instructions: str | None) -> None:
    model = FakeModel(lambda history, tools: reply('{"kcal": 145, "protein_g": 4, "carbs_g": 29}'))
    run(grounding.answer, model, question("q1"), FakeTools(instructions=instructions))
    assert model.requests[0][0] == grounding.SYSTEM_PROMPT


@pytest.mark.parametrize(
    ("instructions", "recorded"), [("Call search_food first.", True), (None, False)]
)
def test_the_results_say_whether_the_server_instructions_were_passed(
    instructions: str | None, recorded: bool
) -> None:
    @contextlib.asynccontextmanager
    async def toolbox() -> Any:
        yield FakeTools(instructions=instructions)

    result = run(
        grounding.run,
        FakeModel(lambda history, tools: reply('{"kcal": 150, "protein_g": 4, "carbs_g": 30}')),
        [question("q1")],
        now=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        commit="abc1234",
        dataset_version="0123456789ab",
        dirty=False,
        questions_version="fedcba987654",
        min_interval=0.0,
        toolbox=toolbox,
    )
    assert result["server_instructions"] is recorded


def test_a_question_stops_at_the_turn_cap() -> None:
    model = FakeModel(lambda history, tools: reply("", grounding.ToolCall("search_food", {})))
    tools = FakeTools()
    outcome = run(grounding.answer, model, question("q1"), tools)
    assert outcome.capped
    assert outcome.turns == grounding.MAX_TURNS
    assert len(model.requests) == grounding.MAX_TURNS
    # The last turn's calls are not run: no turn is left to read their results.
    assert len(tools.calls) == len(outcome.tool_calls) == grounding.MAX_TURNS - 1
    assert isinstance(outcome.parsed, grounding.Failed)
    assert "turn cap" in outcome.parsed.reason


def test_a_busy_model_is_retried_with_backoff() -> None:
    attempts: list[int] = []

    def script(history: Sequence[grounding.Message], tools: Sequence[grounding.ToolSpec]) -> Any:
        attempts.append(1)
        if len(attempts) <= 2:
            return grounding.RetryableModelError("http 429")
        return reply('{"kcal": 145, "protein_g": 4, "carbs_g": 29}')

    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    outcome = run(grounding.answer, FakeModel(script), question("q1"), None, sleep=sleep)
    assert outcome.parsed == numbers(145, 4, 29)
    assert slept == list(grounding.RETRY_DELAYS[:2])


def test_a_model_that_stays_busy_gives_an_api_error() -> None:
    model = FakeModel(lambda history, tools: grounding.RetryableModelError("http 503"))

    async def sleep(seconds: float) -> None:
        pass

    outcome = run(grounding.answer, model, question("q1"), None, sleep=sleep)
    assert outcome.parsed == grounding.ApiError("http 503")
    assert len(model.requests) == len(grounding.RETRY_DELAYS) + 1


def test_a_refused_request_is_a_failed_answer_not_retried() -> None:
    # A 400, 403 or 404 says the request is wrong (a bad key, a schema the API rejects), not
    # that the API is busy, so the model is scored as failing to answer.
    model = FakeModel(lambda history, tools: grounding.ModelError("http 400"))
    outcome = run(grounding.answer, model, question("q1"), None)
    assert outcome.parsed == grounding.Failed("request refused", model_error="http 400")
    assert len(model.requests) == 1


def test_a_request_refused_after_a_tool_call_is_a_failed_answer() -> None:
    def script(history: Sequence[grounding.Message], tools: Sequence[grounding.ToolSpec]) -> Any:
        if len(history) == 1:
            return reply("", grounding.ToolCall("search_food", {"query": "idli"}, "c1"))
        return grounding.ModelError("http 404")

    outcome = run(grounding.answer, FakeModel(script), question("q1"), FakeTools())
    assert outcome.parsed == grounding.Failed("request refused", model_error="http 404")
    assert outcome.turns == 2
    assert [record.name for record in outcome.tool_calls] == ["search_food"]


class FakeClock:
    """Time that moves only when something sleeps, so pacing runs instantly."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_the_pacer_keeps_the_minimum_interval() -> None:
    clock = FakeClock()
    pacer = grounding.Pacer(4.0, clock=clock, sleep=clock.sleep)

    async def scenario() -> None:
        await pacer.wait()  # the first request goes at once
        await pacer.wait()  # straight after: waits the whole interval
        clock.now += 10.0
        await pacer.wait()  # long after: no wait
        clock.now += 1.5
        await pacer.wait()  # part of the interval has passed

    anyio.run(scenario)
    assert clock.slept == [4.0, 2.5]


def test_requests_are_paced_across_questions_arms_and_retries() -> None:
    clock = FakeClock()
    started: list[float] = []
    busy = [True]

    def script(history: Sequence[grounding.Message], tools: Sequence[grounding.ToolSpec]) -> Any:
        started.append(clock.now)
        if busy[0]:
            busy[0] = False
            return grounding.RetryableModelError("http 429")
        return reply('{"kcal": 150, "protein_g": 4, "carbs_g": 30}')

    @contextlib.asynccontextmanager
    async def toolbox() -> Any:
        yield FakeTools()

    result = run(
        grounding.run,
        FakeModel(script),
        [question("q1"), question("q2")],
        now=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        commit="abc1234",
        dataset_version="0123456789ab",
        dirty=False,
        questions_version="fedcba987654",
        min_interval=4.0,
        toolbox=toolbox,
        clock=clock,
        sleep=clock.sleep,
    )

    # One retry, then two questions in each arm: five request starts.
    assert len(started) == 5
    assert all(later - earlier >= 4.0 for earlier, later in itertools.pairwise(started))
    assert result["min_request_interval_s"] == 4.0


def test_an_api_error_is_left_out_of_scoring_and_counted() -> None:
    questions = [question("q1"), question("q2"), question("m1", missing=("kheer",))]
    golden = {q: {"kcal": 200.0, "protein_g": 10.0, "carbs_g": 40.0} for q in ("q1", "q2")}
    outcomes = {
        "q1": grounding.Outcome(numbers(200, 10, 40)),
        "q2": grounding.Outcome(grounding.ApiError("http 429")),
        "m1": grounding.Outcome(grounding.ApiError("http 503")),
    }

    metrics = grounding.score_arm(questions, golden, outcomes, paired=["q1"])

    assert metrics["api_errors"] == 2
    assert metrics["failed_answers"] == 0
    assert metrics["n_numbers"] == 1
    # q2 is not counted as outside 10%: it was never answered.
    assert metrics["within_10pct_kcal"] == 1.0
    assert metrics["mape_kcal"] == 0.0
    # The only missing-dish question had an API error, so there is no share to report.
    assert metrics["missing_reported"] is None


def test_tool_errors_and_latency_reach_the_results() -> None:
    def script(history: Sequence[grounding.Message], tools: Sequence[grounding.ToolSpec]) -> Any:
        if not tools:
            return reply('{"kcal": 150, "protein_g": 4, "carbs_g": 30}')
        if len(history) == 1:
            return reply(
                "",
                grounding.ToolCall("search_food", {"query": "idli"}),
                grounding.ToolCall("get_nutrition", {"food_id": "x"}),
            )
        return reply('{"kcal": 145.1, "protein_g": 4.62, "carbs_g": 28.88}')

    @contextlib.asynccontextmanager
    async def toolbox() -> Any:
        yield FakeTools(error=True)

    questions = [question("q1"), question("m1", missing=("kheer",))]
    result = run(
        grounding.run,
        FakeModel(script),
        questions,
        now=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        commit="abc1234",
        dataset_version="0123456789ab",
        dirty=False,
        questions_version="fedcba987654",
        min_interval=0.0,
        toolbox=toolbox,
    )

    assert result["suite"] == "grounding"
    # Left to the provider's default, as Google advises for Gemini 3 models.
    assert result["temperature"] == "default"
    assert (result["date"], result["generated_at"]) == ("2026-10-21", "2026-10-21T09:00:00+00:00")
    assert (result["model"], result["tier"], result["cost_usd"]) == ("fake-model", "free", 0.0)
    assert (result["commit"], result["dataset_version"], result["n"]) == (
        "abc1234",
        "0123456789ab",
        2,
    )
    assert (result["dirty"], result["questions_version"]) == (False, "fedcba987654")
    assert result["tool_calls"] == 4
    assert result["tool_errors"] == {"count": 4, "by_tool": {"get_nutrition": 2, "search_food": 2}}
    assert result["p95_tool_latency_ms"] == 1.5
    assert result["turns_capped"] == 0
    assert result["tokens"] == {
        "without_tools": {"input": 200, "output": 40},
        "with_tools": {"input": 400, "output": 80},
    }
    assert result["with_tools"]["n_numbers"] == 1
    assert result["n_paired"] == 1
    assert result["with_tools"]["missing_reported"] == 0.0

    rows = {row["id"]: row for row in result["rows"]}
    q1 = rows["q1"]
    assert q1["golden"] == grounding.golden_answers([questions[0]])["q1"]
    assert q1["without_tools"]["answer"] == {"kcal": 150.0, "protein_g": 4.0, "carbs_g": 30.0}
    assert q1["with_tools"]["failure"] is None
    assert [call["name"] for call in q1["with_tools"]["tool_calls"]] == [
        "search_food",
        "get_nutrition",
    ]
    assert rows["m1"]["golden"] is None and rows["m1"]["expect_missing"] == ["kheer"]

    # The headline row comes first, and every summary value is copied from the arms.
    summary = result["summary"]
    assert [row["measure"] for row in summary] == [
        "Calorie error against the dataset totals, MAPE (%)",
        "Protein error against the dataset totals, MAPE (%)",
        "Carbohydrate error against the dataset totals, MAPE (%)",
        "Answers within 10% on calories (share)",
        "Missing dishes reported (share)",
        "Failed answers (count)",
        "API errors (count)",
        "Tool errors (count)",
        "p95 tool latency (ms)",
    ]
    assert summary[0] == {
        "measure": "Calorie error against the dataset totals, MAPE (%)",
        "n_without_tools": 1,
        "n_with_tools": 1,
        "without_tools": result["without_tools"]["mape_kcal"],
        "with_tools": result["with_tools"]["mape_kcal"],
    }
    rows = {row["measure"]: row for row in summary}
    # Without tools there is no data to look a dish up in, so that share has no baseline.
    assert rows["Missing dishes reported (share)"] == {
        "measure": "Missing dishes reported (share)",
        "n_without_tools": None,
        "n_with_tools": 1,
        "without_tools": None,
        "with_tools": 0.0,
    }
    assert rows["Tool errors (count)"] == {
        "measure": "Tool errors (count)",
        "n_without_tools": None,
        "n_with_tools": 4,
        "without_tools": None,
        "with_tools": 4,
    }
    api_errors = next(row for row in summary if row["measure"] == "API errors (count)")
    assert api_errors == {
        "measure": "API errors (count)",
        "n_without_tools": 2,
        "n_with_tools": 2,
        "without_tools": 0,
        "with_tools": 0,
    }
    latency = next(row for row in summary if row["measure"] == "p95 tool latency (ms)")
    assert latency == {
        "measure": "p95 tool latency (ms)",
        "n_without_tools": None,
        "n_with_tools": 4,
        "without_tools": None,
        "with_tools": 1.5,
    }
    assert json.loads(json.dumps(result)) == result


# The google-genai wrapper, tested with plain objects (no network)


COMPUTE_MEAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "$defs": {
        "MealItemIn": {
            "description": "One food in a meal.",
            "properties": {
                "food_id": {
                    "description": "A food id.",
                    "maxLength": 64,
                    "title": "Food Id",
                    "type": "string",
                },
                "amount": {
                    "description": "Above 0.",
                    "exclusiveMinimum": 0,
                    "title": "Amount",
                    "type": "number",
                },
                "unit": {"maxLength": 64, "title": "Unit", "type": "string"},
            },
            "required": ["food_id", "amount", "unit"],
            "title": "MealItemIn",
            "type": "object",
        }
    },
    "properties": {
        "items": {
            "items": {"$ref": "#/$defs/MealItemIn"},
            "maxItems": 30,
            "minItems": 1,
            "title": "Items",
            "type": "array",
        },
        "region": {
            "anyOf": [{"enum": ["north", "south"], "type": "string"}, {"type": "null"}],
            "default": None,
        },
    },
    "required": ["items"],
    "title": "compute_mealArguments",
}


def test_schema_to_function_declaration() -> None:
    spec = grounding.ToolSpec("compute_meal", "Totals for a meal.", COMPUTE_MEAL_SCHEMA)
    declaration = grounding.function_declaration(spec)

    assert declaration.name == "compute_meal"
    assert declaration.description == "Totals for a meal."
    assert declaration.parameters is None
    schema = declaration.parameters_json_schema
    item = schema["$defs"]["MealItemIn"]
    # Keywords outside the subset Gemini documents are dropped, at every depth.
    assert "exclusiveMinimum" not in item["properties"]["amount"]
    assert "maxLength" not in item["properties"]["food_id"]
    assert item["properties"]["amount"] == {
        "description": "Above 0.",
        "title": "Amount",
        "type": "number",
    }
    assert item["required"] == ["food_id", "amount", "unit"]
    assert schema["properties"]["items"] == {
        "items": {"$ref": "#/$defs/MealItemIn"},
        "maxItems": 30,
        "minItems": 1,
        "title": "Items",
        "type": "array",
    }
    assert schema["properties"]["region"]["anyOf"][1] == {"type": "null"}
    # The server's own schema is left as it was.
    assert "exclusiveMinimum" in COMPUTE_MEAL_SCHEMA["$defs"]["MealItemIn"]["properties"]["amount"]


def test_response_to_reply() -> None:
    content = types.Content(
        role="model",
        parts=[
            types.Part(text="Working it out.", thought=True),
            types.Part(text="Here is the total. "),
            types.Part(text='{"kcal": 1, "protein_g": 1, "carbs_g": 1}'),
            types.Part(
                function_call=types.FunctionCall(
                    id="c1", name="search_food", args={"query": "idli"}
                ),
                thought_signature=b"sig",
            ),
        ],
    )
    response = types.GenerateContentResponse(
        candidates=[types.Candidate(content=content, finish_reason=types.FinishReason.STOP)],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=120, candidates_token_count=30, thoughts_token_count=12
        ),
    )

    result = grounding.reply_from_response(response)

    assert result.turn.text == 'Here is the total. {"kcal": 1, "protein_g": 1, "carbs_g": 1}'
    assert result.turn.calls == (grounding.ToolCall("search_food", {"query": "idli"}, "c1"),)
    # The model's own content goes back unchanged, thought signature included.
    assert result.turn.raw is content
    # Thinking tokens are billed as output.
    assert result.usage == grounding.Usage(120, 42)
    assert result.finish_reason == "STOP"


def test_an_empty_response_is_an_empty_turn() -> None:
    result = grounding.reply_from_response(types.GenerateContentResponse())
    assert result.turn == grounding.ModelTurn("", ())
    assert result.usage == grounding.Usage(0, 0)
    assert result.finish_reason is None


def test_history_to_contents() -> None:
    raw = types.Content(role="model", parts=[types.Part(text="kept as sent")])
    call = grounding.ToolCall("get_nutrition", {"food_id": "idli"}, "c7")
    history: list[grounding.Message] = [
        grounding.UserText("2 idlis"),
        grounding.ModelTurn("", (call,), raw),
        grounding.ToolResults(
            (
                grounding.ToolResult(call, {"output": {"grams": 80.0}}, False),
                grounding.ToolResult(grounding.ToolCall("search_food", {}), {"error": "bad"}, True),
            )
        ),
        grounding.ModelTurn("Not sure.", (grounding.ToolCall("search_food", {"query": "x"}),)),
    ]

    contents = grounding.to_contents(history)

    assert contents[0] == types.Content(role="user", parts=[types.Part(text="2 idlis")])
    assert contents[1] is raw
    assert contents[2].role == "user"
    first, second = (part.function_response for part in contents[2].parts or [])
    assert first is not None and second is not None
    assert (first.id, first.name, first.response) == (
        "c7",
        "get_nutrition",
        {"output": {"grams": 80.0}},
    )
    assert (second.id, second.name, second.response) == (None, "search_food", {"error": "bad"})
    rebuilt = contents[3]
    assert rebuilt.role == "model"
    parts = rebuilt.parts or []
    assert parts[0].text == "Not sure."
    assert parts[1].function_call == types.FunctionCall(name="search_food", args={"query": "x"})


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (genai_errors.ClientError(429, {"error": {"message": "quota"}}), True),
        (genai_errors.ServerError(503, {"error": {"message": "overloaded"}}), True),
        (genai_errors.ServerError(500, {}), True),
        (
            genai_errors.ClientError(400, {"error": {"message": "API key not valid. key=abc"}}),
            False,
        ),
        (genai_errors.ClientError(403, {}), False),
        (genai_errors.ClientError(408, {}), True),
    ],
)
def test_api_errors_map_to_model_errors(error: genai_errors.APIError, retryable: bool) -> None:
    mapped = grounding.model_error(error)
    assert isinstance(mapped, grounding.RetryableModelError) is retryable
    assert mapped.kind == f"http {error.code}"
    # The error carries the status code only, never the response text.
    assert str(mapped) == f"http {error.code}"


class StubModels:
    def __init__(self, response: types.GenerateContentResponse) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    async def generate_content(self, **request: Any) -> types.GenerateContentResponse:
        self.requests.append(request)
        return self.response


def test_gemini_client_sends_the_prompt_and_tools_and_no_temperature() -> None:
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=[types.Part(text="ok")]))
        ]
    )
    models = StubModels(response)
    stub = SimpleNamespace(aio=SimpleNamespace(models=models))
    client = grounding.GeminiClient("gemini-test", stub)
    spec = grounding.ToolSpec("compute_meal", "Totals.", COMPUTE_MEAL_SCHEMA)

    result = run(client.generate, grounding.SYSTEM_PROMPT, [grounding.UserText("2 idlis")], [spec])
    run(client.generate, grounding.SYSTEM_PROMPT, [grounding.UserText("2 idlis")], [])

    assert result.turn.text == "ok"
    with_tools, without_tools = models.requests
    assert with_tools["model"] == "gemini-test"
    config = with_tools["config"]
    assert config.system_instruction == grounding.SYSTEM_PROMPT
    # Not set, so the provider's default applies.
    assert config.temperature is None
    assert config.automatic_function_calling.disable is True
    [tool] = config.tools
    assert [d.name for d in tool.function_declarations] == ["compute_meal"]
    assert without_tools["config"].tools is None
    assert without_tools["config"].system_instruction == grounding.SYSTEM_PROMPT


def test_gemini_client_turns_api_errors_into_model_errors() -> None:
    class Failing:
        async def generate_content(self, **request: Any) -> None:
            raise genai_errors.ClientError(429, {"error": {"message": "quota"}})

    client = grounding.GeminiClient(
        "gemini-test", SimpleNamespace(aio=SimpleNamespace(models=Failing()))
    )
    with pytest.raises(grounding.RetryableModelError, match="http 429"):
        run(client.generate, grounding.SYSTEM_PROMPT, [grounding.UserText("q")], [])


def test_a_network_failure_is_retryable() -> None:
    class Unreachable:
        async def generate_content(self, **request: Any) -> None:
            raise httpx.ConnectError("connection refused")

    client = grounding.GeminiClient(
        "gemini-test", SimpleNamespace(aio=SimpleNamespace(models=Unreachable()))
    )
    with pytest.raises(grounding.RetryableModelError, match="network ConnectError"):
        run(client.generate, grounding.SYSTEM_PROMPT, [grounding.UserText("q")], [])


def test_a_tool_call_that_breaks_is_a_tool_error() -> None:
    class Broken:
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> None:
            raise MCPError(code=-32001, message="Request 'tools/call' timed out")

    tools = grounding.McpToolBox(Broken(), [], None)
    result, record = run(tools.call, grounding.ToolCall("search_food", {"query": "idli"}))
    assert result.is_error and record.is_error
    assert result.response == {"error": "tool call failed: MCPError"}
    assert record.latency_ms >= 0


def test_summary_n_is_each_arms_own_count() -> None:
    questions = [question("q1"), question("q2"), question("m1", missing=("kheer",))]
    golden = {q: {"kcal": 200.0, "protein_g": 10.0, "carbs_g": 40.0} for q in ("q1", "q2")}
    without = {
        "q1": grounding.Outcome(numbers(200, 10, 40)),
        "q2": grounding.Outcome(grounding.Failed("no JSON object")),
        "m1": grounding.Outcome(grounding.Missing(("kheer",), has_numbers=False)),
    }
    with_ = {
        "q1": grounding.Outcome(numbers(200, 10, 40)),
        "q2": grounding.Outcome(numbers(210, 10, 40)),
        "m1": grounding.Outcome(grounding.ApiError("http 503")),
    }
    result = grounding.build_result(
        "fake-model",
        questions,
        golden,
        without,
        with_,
        now=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        commit="abc1234",
        dataset_version="0123456789ab",
        dirty=False,
        questions_version="fedcba987654",
        min_interval=0.0,
    )
    n = {row["measure"]: (row["n_without_tools"], row["n_with_tools"]) for row in result["summary"]}
    # MAPE: questions both arms answered with numbers. Within 10%: each arm's questions with
    # items that got a reply.
    assert n["Calorie error against the dataset totals, MAPE (%)"] == (1, 1)
    assert n["Answers within 10% on calories (share)"] == (2, 2)
    # m1 had an API error with tools, so no missing dish was scored there.
    assert n["Missing dishes reported (share)"] == (None, 0)
    assert n["Failed answers (count)"] == (3, 2)
    assert n["API errors (count)"] == (3, 3)


@pytest.mark.parametrize(
    ("error", "requests_per_question"),
    [
        # A refused request is a failed answer, but five in a row still stop the run.
        (grounding.ModelError("http 400"), 1),
        (grounding.RetryableModelError("http 429"), len(grounding.RETRY_DELAYS) + 1),
    ],
)
def test_a_run_stops_after_consecutive_request_failures(
    error: grounding.ModelError, requests_per_question: int
) -> None:
    model = FakeModel(lambda history, tools: error)

    @contextlib.asynccontextmanager
    async def toolbox() -> Any:
        yield FakeTools()

    async def sleep(seconds: float) -> None:
        pass

    with pytest.raises(grounding.GroundingError, match="in a row"):
        run(
            grounding.run,
            model,
            [question(f"q{i}") for i in range(1, 7)],
            now=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
            commit="abc1234",
            dataset_version="0123456789ab",
            dirty=False,
            questions_version="fedcba987654",
            min_interval=0.0,
            toolbox=toolbox,
            sleep=sleep,
        )
    assert grounding.MAX_CONSECUTIVE_API_ERRORS == 5
    assert len(model.requests) == 5 * requests_per_question


def test_an_answer_resets_the_api_error_count() -> None:
    calls: list[int] = []

    def script(history: Sequence[grounding.Message], tools: Sequence[grounding.ToolSpec]) -> Any:
        calls.append(1)
        # Every fifth request answers; the four between fail.
        if len(calls) % 5 == 0:
            return reply('{"kcal": 150, "protein_g": 4, "carbs_g": 30}')
        return grounding.ModelError("http 400")

    @contextlib.asynccontextmanager
    async def toolbox() -> Any:
        yield FakeTools()

    result = run(
        grounding.run,
        FakeModel(script),
        [question(f"q{i}") for i in range(1, 6)],
        now=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        commit="abc1234",
        dataset_version="0123456789ab",
        dirty=False,
        questions_version="fedcba987654",
        min_interval=0.0,
        toolbox=toolbox,
    )
    for arm in ("without_tools", "with_tools"):
        assert result[arm]["failed_answers"] == 4
        assert result[arm]["api_errors"] == 0
    rows = {row["id"]: row for row in result["rows"]}
    assert rows["q1"]["without_tools"]["failure"] == "request refused"
    assert rows["q1"]["without_tools"]["model_error"] == "http 400"
    assert rows["q5"]["without_tools"]["model_error"] is None


@pytest.mark.parametrize(
    "script",
    [
        # A key the API refuses: every request is an API error.
        lambda history, tools: grounding.ModelError("http 400"),
        # Replies, but none that can be read as an answer.
        lambda history, tools: reply("No idea."),
    ],
)
def test_a_run_with_no_answer_in_an_arm_is_an_error(script: Any) -> None:
    @contextlib.asynccontextmanager
    async def toolbox() -> Any:
        yield FakeTools()

    with pytest.raises(grounding.GroundingError, match="no answer"):
        run(
            grounding.run,
            FakeModel(script),
            [question("q1"), question("q2")],
            now=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
            commit="abc1234",
            dataset_version="0123456789ab",
            dirty=False,
            questions_version="fedcba987654",
            min_interval=0.0,
            toolbox=toolbox,
        )


def test_an_arm_with_only_missing_lists_is_an_error() -> None:
    def script(history: Sequence[grounding.Message], tools: Sequence[grounding.ToolSpec]) -> Any:
        if not tools:
            return reply('{"kcal": 150, "protein_g": 4, "carbs_g": 30}')
        return reply('{"missing": ["idli"]}')

    @contextlib.asynccontextmanager
    async def toolbox() -> Any:
        yield FakeTools()

    with pytest.raises(grounding.GroundingError, match="with_tools arm gave no answer"):
        run(
            grounding.run,
            FakeModel(script),
            [question("q1"), question("m1", missing=("kheer",))],
            now=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
            commit="abc1234",
            dataset_version="0123456789ab",
            dirty=False,
            questions_version="fedcba987654",
            min_interval=0.0,
            toolbox=toolbox,
        )


def test_the_gemini_client_has_a_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from google import genai

    built: list[dict[str, Any]] = []

    def record(**kwargs: Any) -> object:
        built.append(kwargs)
        return object()

    monkeypatch.setattr(genai, "Client", record)
    client = grounding.gemini_client("gemini-test", "stand-in")
    assert client.model == "gemini-test"
    [kwargs] = built
    assert kwargs["api_key"] == "stand-in"
    # Milliseconds, as google-genai takes it.
    assert kwargs["http_options"].timeout == grounding.REQUEST_TIMEOUT_MS == 120_000
