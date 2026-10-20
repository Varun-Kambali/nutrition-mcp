import json
from pathlib import Path

import grounding
import pytest

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
    reg = build_registry([])
    regions = {
        region for q in questions for item in q.items for region in reg.get(item.food_id).regions
    }
    assert regions == {"north", "south", "east", "west"}


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
