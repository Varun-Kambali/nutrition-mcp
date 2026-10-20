"""Grounding evaluation: a model's nutrition answers with and without this server's tools.

Each question in evals/datasets/questions.jsonl lists the food ids, amounts and units behind
it. The golden answer is what `queries.meal_totals` computes for those items from the bundled
data, so a model that calls the tools and copies their totals matches it exactly. A question
about a dish that is not in the data has no items and names the dish in `expect_missing`.
"""

import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from nutrition_mcp.config import Settings
from nutrition_mcp.loader import build_registry
from nutrition_mcp.queries import meal_totals
from nutrition_mcp.schemas import MealItemIn

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "evals" / "datasets" / "questions.jsonl"

# The keys of a final answer, and the meal total each one is scored against.
ANSWER_KEYS = ("kcal", "protein_g", "carbs_g")
GOLDEN_SOURCE = {"kcal": "kcal", "protein_g": "protein_g", "carbs_g": "carbs_available_g"}

Golden = dict[str, float]


class GroundingError(Exception):
    """A problem with the questions file or a golden answer. The message names the row."""


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

    Raises GroundingError when an item cannot be worked out or a total is missing or flagged
    incomplete, since a floor is no answer to score against.
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
            answer[key] = value
        golden[question.id] = answer
    return golden
