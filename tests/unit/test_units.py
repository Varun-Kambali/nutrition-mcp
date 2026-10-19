from typing import Any

import pytest

from nutrition_mcp.models import Food, Nutrients
from nutrition_mcp.units import UnitError, normalise_unit, to_grams, valid_units

DEFAULT_KATORI_G = 150.0


def ingredient(**overrides: Any) -> Food:
    data: dict[str, Any] = {
        "id": "paneer",
        "kind": "ingredient",
        "name": "Paneer",
        "aliases": [],
        "regions": [],
        "diet": "veg",
        "source_ids": ["usda_fdc"],
        "per_100g": Nutrients(kcal=321),
        "incomplete": {},
        "gi": None,
        "gi_ref": None,
        "measures": {},
        "serving_g": None,
        "assumptions": None,
        "density_g_per_ml": None,
    }
    data.update(overrides)
    return Food.model_validate(data)


def dish(**overrides: Any) -> Food:
    data: dict[str, Any] = {
        "id": "roti_wheat",
        "kind": "dish",
        "name": "Roti",
        "regions": ["north"],
        "measures": {"piece": 40.0, "roti": 40.0, "serving": 80.0},
        "serving_g": 80.0,
        "assumptions": "no added fat",
    }
    data.update(overrides)
    return ingredient(**data)


def test_grams_passthrough() -> None:
    grams, assumption = to_grams(ingredient(), 250, "g", DEFAULT_KATORI_G)
    assert grams == 250.0
    assert assumption == "250 g"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("g", "g"),
        ("gram", "g"),
        ("Grams", "g"),
        ("  GM ", "g"),
        ("Rotis", "rotis"),
        ("  Katori", "katori"),
    ],
)
def test_normalise_unit(raw: str, expected: str) -> None:
    assert normalise_unit(raw) == expected


def test_grams_spelled_out() -> None:
    grams, assumption = to_grams(ingredient(), 250, "grams", DEFAULT_KATORI_G)
    assert grams == 250.0
    assert assumption == "250 g"


def test_dish_measure_used() -> None:
    grams, assumption = to_grams(dish(), 2, "piece", DEFAULT_KATORI_G)
    assert grams == 80.0
    assert assumption == "2 piece = 80 g"
    assert to_grams(dish(), 1, "piece", DEFAULT_KATORI_G)[1] == "1 piece = 40 g"


@pytest.mark.parametrize(
    ("unit", "expected_grams"),
    [
        ("rotis", 80.0),
        ("Pieces", 80.0),
        ("katoris", 300.0),
        ("KATORI", 300.0),
        ("servings", 160.0),
    ],
)
def test_plural_units(unit: str, expected_grams: float) -> None:
    grams, _ = to_grams(dish(), 2, unit, DEFAULT_KATORI_G)
    assert grams == expected_grams


def test_plural_of_a_word_ending_in_s() -> None:
    milk = ingredient(density_g_per_ml=1.03)
    grams, assumption = to_grams(milk, 2, "glasses", DEFAULT_KATORI_G)
    assert grams == pytest.approx(2 * 250 * 1.03)
    assert assumption.startswith("2 glass = ")


def test_exact_unit_wins_over_its_singular() -> None:
    food = dish(measures={"pieces": 10.0, "piece": 40.0})
    assert to_grams(food, 1, "pieces", DEFAULT_KATORI_G)[0] == 10.0
    assert to_grams(food, 1, "piece", DEFAULT_KATORI_G)[0] == 40.0


def test_default_katori_for_dishes() -> None:
    grams, assumption = to_grams(dish(), 1, "katori", DEFAULT_KATORI_G)
    assert grams == 150.0
    assert assumption == "1 katori = 150 g (default katori)"


def test_default_katori_follows_the_setting() -> None:
    assert to_grams(dish(), 2, "katori", 180.0)[0] == 360.0


def test_dish_own_katori_beats_the_default() -> None:
    food = dish(measures={"katori": 120.0})
    grams, assumption = to_grams(food, 1, "katori", DEFAULT_KATORI_G)
    assert grams == 120.0
    assert assumption == "1 katori = 120 g"


def test_katori_rejected_for_ingredient() -> None:
    with pytest.raises(UnitError) as exc_info:
        to_grams(ingredient(), 1, "katori", DEFAULT_KATORI_G)
    assert "katori" not in exc_info.value.valid_units
    assert "g" in exc_info.value.valid_units


def test_katori_of_roti_rejected() -> None:
    # A plain roti entry with no measures of its own: a katori of it is not a real portion.
    roti = ingredient(id="roti", name="Roti")
    with pytest.raises(UnitError) as exc_info:
        to_grams(roti, 1, "katori", DEFAULT_KATORI_G)
    assert exc_info.value.valid_units == ["g"]
    assert "valid units: g" in str(exc_info.value)


def test_ml_needs_density() -> None:
    with pytest.raises(UnitError) as exc_info:
        to_grams(ingredient(), 200, "ml", DEFAULT_KATORI_G)
    assert "g" in exc_info.value.valid_units
    assert "ml" not in exc_info.value.valid_units
    assert "paneer" in str(exc_info.value)


def test_ml_uses_density() -> None:
    milk = ingredient(id="milk", name="Milk", density_g_per_ml=1.03)
    grams, assumption = to_grams(milk, 200, "ml", DEFAULT_KATORI_G)
    assert grams == pytest.approx(206.0)
    assert assumption == "200 ml = 206 g at 1.03 g/ml"


@pytest.mark.parametrize(
    ("unit", "expected_grams"),
    [("tbsp", 7.5), ("tsp", 2.5), ("cup", 120.0), ("glass", 125.0)],
)
def test_volume_defaults_scale_with_density(unit: str, expected_grams: float) -> None:
    grams, _ = to_grams(ingredient(density_g_per_ml=0.5), 1, unit, DEFAULT_KATORI_G)
    assert grams == pytest.approx(expected_grams)


def test_volume_assumption_shows_the_millilitres() -> None:
    milk = ingredient(density_g_per_ml=1.03)
    _, assumption = to_grams(milk, 2, "tbsp", DEFAULT_KATORI_G)
    assert assumption == "2 tbsp = 30.9 g (15 ml each at 1.03 g/ml)"


def test_food_measure_beats_volume_default() -> None:
    ghee = ingredient(density_g_per_ml=0.9, measures={"tbsp": 12.0})
    grams, assumption = to_grams(ghee, 2, "tbsp", DEFAULT_KATORI_G)
    assert grams == 24.0
    assert assumption == "2 tbsp = 24 g"


def test_food_measure_without_density_is_valid() -> None:
    sugar = ingredient(measures={"tsp": 4.0})
    assert to_grams(sugar, 3, "tsp", DEFAULT_KATORI_G)[0] == 12.0


def test_a_measure_named_g_does_not_replace_grams() -> None:
    food = ingredient(measures={"g": 40.0})
    assert to_grams(food, 100, "g", DEFAULT_KATORI_G)[0] == 100.0


def test_returned_grams_are_not_rounded() -> None:
    grams, assumption = to_grams(dish(measures={"piece": 33.333}), 1, "piece", DEFAULT_KATORI_G)
    assert grams == 33.333
    assert assumption == "1 piece = 33.3 g"


@pytest.mark.parametrize("amount", [0, -1, -0.5, float("nan")])
def test_non_positive_amount_rejected(amount: float) -> None:
    with pytest.raises(ValueError, match="amount must be greater than 0") as exc_info:
        to_grams(ingredient(), amount, "g", DEFAULT_KATORI_G)
    assert f"{amount:g}" in str(exc_info.value)


def test_over_5000_g_rejected() -> None:
    with pytest.raises(ValueError, match="5000 g") as exc_info:
        to_grams(ingredient(), 100000, "g", DEFAULT_KATORI_G)
    assert not isinstance(exc_info.value, UnitError)


def test_limit_applies_after_conversion() -> None:
    with pytest.raises(ValueError, match="5000 g"):
        to_grams(dish(), 200, "piece", DEFAULT_KATORI_G)


def test_exactly_5000_g_is_allowed() -> None:
    assert to_grams(ingredient(), 5000, "g", DEFAULT_KATORI_G)[0] == 5000.0
    with pytest.raises(ValueError, match="5000 g"):
        to_grams(ingredient(), 5000.5, "g", DEFAULT_KATORI_G)


def test_unknown_unit_names_the_unit_and_lists_valid_ones() -> None:
    with pytest.raises(UnitError) as exc_info:
        to_grams(dish(), 1, "bowl", DEFAULT_KATORI_G)
    error = exc_info.value
    assert isinstance(error, ValueError)
    assert error.valid_units == ["g", "piece", "roti", "serving", "katori"]
    assert "'bowl'" in str(error)
    assert "roti_wheat" in str(error)
    assert "valid units: g, piece, roti, serving, katori" in str(error)


def test_empty_unit_is_unknown() -> None:
    with pytest.raises(UnitError):
        to_grams(dish(), 1, "", DEFAULT_KATORI_G)


def test_valid_units_for_a_plain_ingredient() -> None:
    assert valid_units(ingredient(), DEFAULT_KATORI_G) == ["g"]


def test_valid_units_for_an_ingredient_with_density() -> None:
    food = ingredient(density_g_per_ml=1.03)
    assert valid_units(food, DEFAULT_KATORI_G) == ["g", "ml", "tbsp", "tsp", "cup", "glass"]


def test_valid_units_list_a_dish_default_katori_after_its_own_measures() -> None:
    assert valid_units(dish(), DEFAULT_KATORI_G) == ["g", "piece", "roti", "serving", "katori"]


def test_valid_units_do_not_repeat_a_measure_the_food_defines() -> None:
    food = dish(measures={"katori": 120.0, "piece": 40.0}, density_g_per_ml=1.0)
    assert valid_units(food, DEFAULT_KATORI_G) == [
        "g",
        "katori",
        "piece",
        "ml",
        "tbsp",
        "tsp",
        "cup",
        "glass",
    ]
    own_tbsp = ingredient(density_g_per_ml=1.0, measures={"tbsp": 12.0})
    assert valid_units(own_tbsp, DEFAULT_KATORI_G) == ["g", "tbsp", "ml", "tsp", "cup", "glass"]
