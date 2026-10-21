import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import grounding
import pytest

from nutrition_mcp.schemas import MealItemIn

pytestmark = pytest.mark.anyio

QUESTION = grounding.Question(
    id="q01",
    question="How much protein is in 2 rotis and a katori of rajma?",
    items=(
        MealItemIn(food_id="chapati", amount=2, unit="roti"),
        MealItemIn(food_id="rajma_masala", amount=1, unit="katori"),
    ),
)


def reply(text: str = "", *calls: grounding.ToolCall) -> grounding.ModelReply:
    return grounding.ModelReply(grounding.ModelTurn(text, calls), grounding.Usage(50, 10))


class ScriptedModel:
    """Searches for each food, computes the meal with the ids it found, then answers.

    Without tools it answers from a fixed guess, as a model working from memory would.
    """

    model = "scripted-model"

    async def generate(
        self,
        system: str,
        history: Sequence[grounding.Message],
        tools: Sequence[grounding.ToolSpec],
    ) -> grounding.ModelReply:
        if not tools:
            return reply('{"kcal": 300, "protein_g": 12, "carbs_g": 45}')
        last = history[-1]
        if isinstance(last, grounding.UserText):
            assert {spec.name for spec in tools} >= {"search_food", "compute_meal"}
            return reply(
                "",
                grounding.ToolCall("search_food", {"query": "roti"}, "s1"),
                grounding.ToolCall("search_food", {"query": "rajma"}, "s2"),
            )
        assert isinstance(last, grounding.ToolResults)
        if last.results[0].call.name == "search_food":
            roti, rajma = (
                next(
                    food["id"]
                    for food in result.response["output"]["results"]
                    if food["kind"] == "dish"
                )
                for result in last.results
            )
            items = [
                {"food_id": roti, "amount": 2, "unit": "roti"},
                {"food_id": rajma, "amount": 1, "unit": "katori"},
            ]
            return reply("", grounding.ToolCall("compute_meal", {"items": items}, "m1"))
        total: dict[str, Any] = last.results[0].response["output"]["total"]
        answer = {
            "kcal": total["kcal"],
            "protein_g": total["protein_g"],
            "carbs_g": total["carbs_available_g"],
        }
        return reply(f"From compute_meal:\n```json\n{json.dumps(answer)}\n```")


async def test_a_scripted_model_answers_through_the_stdio_server() -> None:
    result = await grounding.run(
        ScriptedModel(),
        [QUESTION],
        now=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        commit="abc1234",
        dataset_version="0123456789ab",
    )

    [row] = result["rows"]
    golden = grounding.golden_answers([QUESTION])["q01"]
    assert row["with_tools"]["answer"] == golden
    assert row["with_tools"]["ape"] == {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0}
    assert row["with_tools"]["turns"] == 3
    assert [call["name"] for call in row["with_tools"]["tool_calls"]] == [
        "search_food",
        "search_food",
        "compute_meal",
    ]
    assert all(call["latency_ms"] > 0 for call in row["with_tools"]["tool_calls"])
    assert row["without_tools"]["answer"] == {"kcal": 300.0, "protein_g": 12.0, "carbs_g": 45.0}
    assert row["without_tools"]["tool_calls"] == []

    assert result["tool_calls"] == 3
    assert result["tool_errors"] == {"count": 0, "by_tool": {}}
    assert result["p95_tool_latency_ms"] is not None and result["p95_tool_latency_ms"] > 0
    assert result["with_tools"]["mape_kcal"] == 0.0
    assert result["with_tools"]["within_10pct_kcal"] == 1.0
    assert result["without_tools"]["n"] == 1


async def test_the_server_gets_none_of_the_callers_data_folders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A folder that does not exist makes the server exit at startup if it ever reads it.
    monkeypatch.setenv("NUTRITION_MCP_DATA_DIRS", "/no/such/folder")
    async with grounding.stdio_toolbox() as tools:
        assert {spec.name for spec in tools.specs} == {
            "search_food",
            "get_nutrition",
            "compute_meal",
            "find_foods",
            "compute_targets",
        }
        result, record = await tools.call(grounding.ToolCall("get_nutrition", {"food_id": "x"}))
    assert result.is_error and record.is_error
    assert "error" in result.response
    assert record.latency_ms > 0
