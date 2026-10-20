import json
from pathlib import Path
from typing import Any

import build_references
import pytest
from build_ingredients import BuildError

from nutrition_mcp.models import Dish, Ingredient
from nutrition_mcp.source_mapping import usda_to_nutrients

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _ingredient(ingredient_id: str, source_id: str, record: str) -> Ingredient:
    return Ingredient.model_validate(
        {
            "id": ingredient_id,
            "name": ingredient_id,
            "aliases": [],
            "diet": "vegan",
            "source_id": source_id,
            "source_record": record,
            "per_100g": {},
        }
    )


DISHES = [
    Dish.model_validate(
        {
            "id": "dal",
            "name": "Dal",
            "aliases": [],
            "regions": ["south"],
            "diet": "vegan",
            "servings": 1,
            "cooked_weight_g": 400,
            "ingredients": [{"ingredient_id": "toor_dal", "grams": 100}],
            "measures": {},
            "assumptions": "No added fat.",
            "source_id": "test_recipes",
        }
    )
]
INGREDIENTS = [_ingredient("toor_dal", "cofid_2021", "13-104")]


def _map(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "reference_map.csv"
    path.write_text("dish_id,source,record,notes\n" + "".join(f"{row}\n" for row in rows))
    return path


def test_a_valid_map_is_read(tmp_path: Path) -> None:
    rows = build_references.read_reference_map(
        _map(tmp_path, "dal,cofid,15-779,Dal with butter"), DISHES, INGREDIENTS
    )
    assert rows == [
        {"dish_id": "dal", "source": "cofid", "record": "15-779", "notes": "Dal with butter"}
    ]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (["idli,cofid,15-779,"], "'idli' is not a bundled dish"),
        (["dal,cofid,15-779,", "dal,usda,2707427,"], "second reference for 'dal'"),
        (["dal,cofid,13-104,"], "13-104 is an ingredient of dal"),
        (["dal,ifct,A001,"], "source must be one of usda, cofid"),
    ],
)
def test_the_map_rejects_a_bad_row(tmp_path: Path, rows: list[str], message: str) -> None:
    with pytest.raises(BuildError, match=message):
        build_references.read_reference_map(_map(tmp_path, *rows), DISHES, INGREDIENTS)


def test_the_map_needs_its_header(tmp_path: Path) -> None:
    path = tmp_path / "reference_map.csv"
    path.write_text("dish_id,record\ndal,15-779\n")
    with pytest.raises(BuildError, match="header"):
        build_references.read_reference_map(path, DISHES, INGREDIENTS)


def _usda_food(data_type: str) -> dict[str, Any]:
    food: dict[str, Any] = json.loads((FIXTURES / "usda_172436.json").read_text())
    food["dataType"] = data_type
    return food


def test_a_usda_reference_must_be_a_survey_food() -> None:
    # The fixture is SR Legacy pigeon peas: an ingredient record, never a dish reference.
    with pytest.raises(BuildError, match="is SR Legacy, expected Survey"):
        build_references.usda_reference("dal", "172436", _usda_food("SR Legacy"))


def test_a_usda_survey_food_becomes_a_reference() -> None:
    food = _usda_food("Survey (FNDDS)")
    reference = build_references.usda_reference("dal", "172436", food)
    assert (reference.dish_id, reference.source_id, reference.record) == (
        "dal",
        "usda_fdc",
        "172436",
    )
    assert reference.description.endswith("[Survey (FNDDS)]")
    assert reference.per_100g == usda_to_nutrients(food)


def test_a_dataset_error_is_reported_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_references, "bundled_dir", lambda: tmp_path / "missing")
    assert build_references.main([]) == 1
    assert capsys.readouterr().err.startswith("error: ")
