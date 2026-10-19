from typing import Any

import pytest
from pydantic import ValidationError

from nutrition_mcp.models import Dish, Food, Ingredient, Nutrients, Source
from nutrition_mcp.nutrients import HEAT_SENSITIVE, NUTRIENT_KEYS, NUTRIENTS


def dish_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "rajma_masala",
        "name": "Rajma masala",
        "aliases": ["rajma"],
        "regions": ["north"],
        "diet": "veg",
        "servings": 4,
        "cooked_weight_g": 900,
        "ingredients": [{"ingredient_id": "rajma_dried", "grams": 200}],
        "measures": {"katori": 150},
        "assumptions": "1 tsp oil per serving",
        "gi": 29,
        "gi_ref": "Atkinson 2021",
        "source_id": "ifct_2017",
    }
    data.update(overrides)
    return data


def ingredient_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "toor_dal",
        "name": "Toor dal",
        "aliases": ["arhar dal"],
        "diet": "vegan",
        "source_id": "usda_fdc",
        "source_record": "172420",
        "per_100g": {"kcal": 343},
        "gi": None,
        "gi_ref": None,
        "density_g_per_ml": None,
    }
    data.update(overrides)
    return data


def test_nutrient_keys_order() -> None:
    assert NUTRIENT_KEYS[0] == "kcal"
    assert len(NUTRIENT_KEYS) == 16
    assert tuple(spec.key for spec in NUTRIENTS) == NUTRIENT_KEYS
    assert list(Nutrients.model_fields) == list(NUTRIENT_KEYS)


def test_nutrient_specs_mark_lower_is_better() -> None:
    lower = {spec.key for spec in NUTRIENTS if spec.lower_is_better}
    assert lower == {"kcal", "sat_fat_g", "sugars_g", "sodium_mg"}


def test_heat_sensitive_nutrients() -> None:
    assert set(HEAT_SENSITIVE) == {"folate_ug", "vit_c_mg", "vit_b12_ug"}
    assert set(NUTRIENT_KEYS).issuperset(HEAT_SENSITIVE)


def test_nutrients_default_to_missing_not_zero() -> None:
    nutrients = Nutrients()
    assert all(nutrients.get(key) is None for key in NUTRIENT_KEYS)


def test_nutrients_reject_negative() -> None:
    with pytest.raises(ValidationError):
        Nutrients(kcal=-1)


def test_nutrients_reject_unknown_key() -> None:
    with pytest.raises(ValidationError):
        Nutrients.model_validate({"kcal": 1, "magnesium_mg": 5})


def test_nutrients_scaled_keeps_none() -> None:
    scaled = Nutrients(kcal=100, iron_mg=None).scaled(1.5)
    assert scaled.kcal == 150
    assert scaled.iron_mg is None


def test_nutrients_scaled_keeps_zero_as_zero() -> None:
    assert Nutrients(sugars_g=0).scaled(2).sugars_g == 0


def test_nutrients_get_returns_value_or_none() -> None:
    nutrients = Nutrients(kcal=343, iron_mg=None)
    assert nutrients.get("kcal") == 343
    assert nutrients.get("iron_mg") is None


def test_nutrients_get_unknown_key_raises() -> None:
    with pytest.raises(KeyError):
        Nutrients().get("magnesium_mg")


def test_dish_requires_fat_assumption() -> None:
    with pytest.raises(ValidationError, match="oil"):
        Dish.model_validate(dish_data(assumptions="1 katori"))
    assert Dish.model_validate(dish_data(assumptions="1 tsp oil per serving"))


@pytest.mark.parametrize("assumptions", ["2 tsp Ghee", "No added fat", "1 tsp OIL"])
def test_dish_fat_assumption_is_case_insensitive(assumptions: str) -> None:
    assert Dish.model_validate(dish_data(assumptions=assumptions))


def test_dish_fat_assumption_ignores_words_that_merely_contain_oil() -> None:
    with pytest.raises(ValidationError):
        Dish.model_validate(dish_data(assumptions="1 katori boiled rice"))


def test_dish_regions_unique_and_known() -> None:
    with pytest.raises(ValidationError):
        Dish.model_validate(dish_data(regions=["central"]))
    with pytest.raises(ValidationError):
        Dish.model_validate(dish_data(regions=["south", "south"]))
    with pytest.raises(ValidationError):
        Dish.model_validate(dish_data(regions=[]))


def test_dish_needs_at_least_one_ingredient() -> None:
    with pytest.raises(ValidationError):
        Dish.model_validate(dish_data(ingredients=[]))


def test_dish_rejects_non_positive_amounts() -> None:
    with pytest.raises(ValidationError):
        Dish.model_validate(dish_data(cooked_weight_g=0))
    with pytest.raises(ValidationError):
        Dish.model_validate(dish_data(servings=0))
    with pytest.raises(ValidationError):
        Dish.model_validate(dish_data(measures={"katori": 0}))
    with pytest.raises(ValidationError):
        Dish.model_validate(dish_data(ingredients=[{"ingredient_id": "rajma_dried", "grams": 0}]))


def test_id_must_be_slug() -> None:
    with pytest.raises(ValidationError):
        Dish.model_validate(dish_data(id="Rajma Masala"))
    with pytest.raises(ValidationError):
        Ingredient.model_validate(ingredient_data(id="Toor Dal"))


def test_ingredient_gi_and_density_bounds() -> None:
    assert Ingredient.model_validate(ingredient_data(gi=110, density_g_per_ml=1.03))
    with pytest.raises(ValidationError):
        Ingredient.model_validate(ingredient_data(gi=111))
    with pytest.raises(ValidationError):
        Ingredient.model_validate(ingredient_data(density_g_per_ml=0))


def test_source_parses_retrieved_date() -> None:
    source = Source.model_validate(
        {
            "id": "usda_fdc",
            "name": "USDA FoodData Central",
            "url": "https://fdc.nal.usda.gov",
            "license": "CC0 1.0",
            "version": "2026-04",
            "retrieved": "2026-10-19",
            "attribution": "U.S. Department of Agriculture",
        }
    )
    assert source.retrieved.isoformat() == "2026-10-19"


def test_food_holds_a_resolved_record() -> None:
    food = Food(
        id="rajma_masala",
        kind="dish",
        name="Rajma masala",
        aliases=[],
        regions=["north"],
        diet="veg",
        source_ids=["ifct_2017", "usda_fdc"],
        per_100g=Nutrients(kcal=120, iron_mg=None),
        incomplete={"iron_mg": ["rajma_dried"]},
        gi=None,
        gi_ref=None,
        measures={"katori": 150},
        serving_g=225,
        assumptions="1 tsp oil per serving",
        density_g_per_ml=None,
    )
    assert food.per_100g.iron_mg is None
    assert food.incomplete["iron_mg"] == ["rajma_dried"]
