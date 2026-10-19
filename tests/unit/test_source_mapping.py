import json
from pathlib import Path
from typing import Any

import pytest

from nutrition_mcp.nutrients import NUTRIENT_KEYS
from nutrition_mcp.source_mapping import (
    COFID_COLUMNS,
    USDA_IDS,
    cofid_to_nutrients,
    parse_cofid_value,
    usda_density_g_per_ml,
    usda_to_nutrients,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"

# CoFID 2021 food code 12-495, "Cheese, Paneer", as the spreadsheet stores it: strings, with
# "Tr" for trace and "N" for not measured.
PANEER_ROW: dict[str, str | float | None] = {
    "Energy (kcal) (kcal)": "328",
    "Protein (g)": "26.0",
    "Fat (g)": "24.5",
    "Satd FA /100g fd (g)": "15.43",
    "Carbohydrate (g)": "0.9",
    "Total sugars (g)": "0.9",
    "AOAC fibre (g)": "0.0",
    "Sodium (mg)": "19",
    "Iron (mg)": "Tr",
    "Calcium (mg)": "537",
    "Zinc (mg)": "3.2",
    "Folate (µg)": "25",
    "Retinol Equivalent (µg)": "254",
    "Vitamin B12 (µg)": "N",
    "Vitamin C (mg)": "Tr",
    "Vitamin D (µg)": "N",
}


def usda_fixture(fdc_id: int) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"usda_{fdc_id}.json").read_text("utf-8"))
    return data


def nutrient(nutrient_id: int, amount: float, unit: str = "g") -> dict[str, Any]:
    return {"nutrient": {"id": nutrient_id, "unitName": unit}, "amount": amount}


def test_mappings_cover_every_nutrient() -> None:
    assert set(USDA_IDS) | {"carbs_available_g"} == set(NUTRIENT_KEYS)
    assert set(COFID_COLUMNS) == set(NUTRIENT_KEYS)


def test_usda_sr_legacy() -> None:
    values = usda_to_nutrients(usda_fixture(172436))
    assert values.kcal == 343.0
    assert values.protein_g == 21.7
    assert values.carbs_available_g == pytest.approx(47.78)
    assert values.sugars_g is None
    assert values.folate_ug == 456.0
    # A reported zero stays zero; only an absent nutrient is None.
    assert values.vit_b12_ug == 0.0


def test_usda_foundation_energy_fallback() -> None:
    values = usda_to_nutrients(usda_fixture(2512381))
    assert values.kcal == pytest.approx(369.637321)
    assert values.vit_c_mg is None


def test_usda_first_listed_id_wins() -> None:
    food = {"foodNutrients": [nutrient(2047, 350.0, "kcal"), nutrient(1008, 340.0, "kcal")]}
    assert usda_to_nutrients(food).kcal == 340.0


def test_usda_carbs_need_both_values_and_never_go_negative() -> None:
    assert usda_to_nutrients({"foodNutrients": [nutrient(1005, 10.0)]}).carbs_available_g is None
    assert usda_to_nutrients({"foodNutrients": [nutrient(1079, 3.0)]}).carbs_available_g is None
    both = {"foodNutrients": [nutrient(1005, 2.0), nutrient(1079, 3.0)]}
    assert usda_to_nutrients(both).carbs_available_g == 0.0


def test_usda_entry_without_amount_is_missing() -> None:
    food = {"foodNutrients": [{"nutrient": {"id": 1003, "unitName": "g"}}]}
    assert usda_to_nutrients(food).protein_g is None


def test_usda_rejects_unexpected_unit() -> None:
    # Vitamin D in IU instead of µg would be 40 times too large if it slipped through.
    with pytest.raises(ValueError, match="vit_d_ug"):
        usda_to_nutrients({"foodNutrients": [nutrient(1114, 40.0, "IU")]})


def test_usda_density_from_cup_portion() -> None:
    # 1 cup of pigeon peas weighs 205 g; a cup is 236.588 ml.
    assert usda_density_g_per_ml(usda_fixture(172436)) == pytest.approx(205.0 / 236.588)
    assert usda_density_g_per_ml(usda_fixture(2512381)) is None


def test_usda_density_from_tablespoon_portion() -> None:
    food = {
        "foodPortions": [
            {"amount": 1.0, "gramWeight": 13.6, "measureUnit": {"name": "tablespoon"}},
        ]
    }
    assert usda_density_g_per_ml(food) == pytest.approx(13.6 / 14.787)
    sr_legacy = {
        "foodPortions": [
            {
                "amount": 2.0,
                "gramWeight": 27.2,
                "modifier": "tbsp",
                "measureUnit": {"name": "undetermined"},
            },
        ]
    }
    assert usda_density_g_per_ml(sr_legacy) == pytest.approx(13.6 / 14.787)


def test_parse_cofid_value() -> None:
    assert parse_cofid_value("Tr") == 0.0
    assert parse_cofid_value("N") is None
    assert parse_cofid_value("") is None
    assert parse_cofid_value("26.0") == 26.0
    assert parse_cofid_value(None) is None
    assert parse_cofid_value(12.5) == 12.5


def test_parse_cofid_value_rejects_unknown_text() -> None:
    with pytest.raises(ValueError, match="'n/a'"):
        parse_cofid_value("n/a")


def test_cofid_paneer() -> None:
    values = cofid_to_nutrients(PANEER_ROW)
    assert values.protein_g == 26.0
    assert values.kcal == 328
    assert values.iron_mg == 0.0
    assert values.vit_d_ug is None
    assert values.carbs_available_g == 0.9
    assert values.vit_a_ug == 254


def test_cofid_missing_column_fails() -> None:
    # A renamed column must stop the build, not turn into a column of missing values.
    row = {key: value for key, value in PANEER_ROW.items() if key != "Folate (µg)"}
    with pytest.raises(KeyError, match="Folate"):
        cofid_to_nutrients(row)
