from pathlib import Path

import pytest
from pydantic import ValidationError

from nutrition_mcp.config import Settings
from nutrition_mcp.loader import build_registry
from nutrition_mcp.models import Nutrients
from nutrition_mcp.nutrition import scale, sum_nutrients
from nutrition_mcp.queries import filter_foods, meal_totals, nutrition_for, rank_foods
from nutrition_mcp.registry import Registry, UnknownFood
from nutrition_mcp.schemas import MealItemIn
from nutrition_mcp.units import UnitError

FIXTURES = Path(__file__).parent.parent / "fixtures"
MINI = FIXTURES / "mini_dataset"
QUERY = FIXTURES / "query_dataset"

SETTINGS = Settings(default_katori_g=150.0)


@pytest.fixture
def reg(no_bundled_data: Path) -> Registry:
    # mini_dataset: toor_dal, water, ghee, test_dal. query_dataset adds a spice blend with no
    # iron or carbohydrate value, a dish that uses it (test_curry), a countable dish
    # (test_roti) and an egg and a non-vegetarian ingredient for the diet filter.
    return build_registry([MINI, QUERY])


def item(food_id: str, amount: float, unit: str) -> MealItemIn:
    return MealItemIn(food_id=food_id, amount=amount, unit=unit)


# Arithmetic helpers


def test_scale_multiplies_known_values_and_keeps_missing() -> None:
    scaled = scale(Nutrients(kcal=100, iron_mg=None), 250)
    assert scaled.kcal == 250
    assert scaled.iron_mg is None


def test_sum_nutrients_adds_known_values_and_names_the_parts_that_lack_one() -> None:
    total, lacking = sum_nutrients([("a", Nutrients(kcal=10, iron_mg=1)), ("b", Nutrients(kcal=5))])
    assert total.kcal == 15
    # b has no iron, so 1 is a floor and the part is named.
    assert total.iron_mg == 1
    assert lacking["iron_mg"] == ["b"]
    assert "kcal" not in lacking
    # Neither part has protein: the total is missing, not 0.
    assert total.protein_g is None
    assert lacking["protein_g"] == ["a", "b"]


def test_sum_nutrients_names_a_repeated_part_once() -> None:
    _, lacking = sum_nutrients([("a", Nutrients(kcal=1)), ("a", Nutrients(kcal=2))])
    assert lacking["protein_g"] == ["a"]


# nutrition_for


def test_nutrition_for_scales(reg: Registry) -> None:
    result = nutrition_for(reg, SETTINGS, "test_dal", 200, "g")
    assert result.nutrients.kcal == 171.5
    assert result.nutrients.protein_g == 11.0
    assert result.grams == 200.0
    assert (result.amount, result.unit) == (200, "g")
    assert result.measure_assumption == "200 g"
    assert result.food.id == "test_dal"
    assert result.sources == reg.get("test_dal").source_ids


def test_nutrition_for_rounds_to_two_decimals(reg: Registry) -> None:
    result = nutrition_for(reg, SETTINGS, "test_roti", 1, "piece")
    assert result.grams == 40.0
    # 340 kcal of flour over a 120 g cooked weight is 283.333 per 100 g; 40 g of it is 113.333.
    assert result.nutrients.kcal == 113.33


def test_nutrition_for_keeps_missing_values_missing(reg: Registry) -> None:
    result = nutrition_for(reg, SETTINGS, "test_dal", 100, "g")
    assert result.nutrients.vit_b12_ug is None
    assert result.incomplete == reg.get("test_dal").incomplete
    assert result.incomplete["vit_d_ug"] == ["toor_dal"]


def test_dish_output_flags_heat_sensitive(reg: Registry) -> None:
    dish = nutrition_for(reg, SETTINGS, "test_dal", 100, "g")
    # Vitamin B12 has no value for this dish, so only the other two are raw-ingredient totals.
    assert dish.raw_ingredient_totals == ["folate_ug", "vit_c_mg"]
    ingredient = nutrition_for(reg, SETTINGS, "toor_dal", 100, "g")
    assert ingredient.raw_ingredient_totals == []


def test_dish_states_its_oil_assumption(reg: Registry) -> None:
    dish = nutrition_for(reg, SETTINGS, "test_dal", 100, "g")
    assert dish.oil_assumption == "No added fat. The dal is boiled in water only."
    assert nutrition_for(reg, SETTINGS, "toor_dal", 100, "g").oil_assumption is None


def test_glycemic_load_from_the_portion(reg: Registry) -> None:
    ingredient = nutrition_for(reg, SETTINGS, "toor_dal", 100, "g")
    assert (ingredient.gi, ingredient.gi_ref) == (28, "test_gi: Toor dal (Table 1, T1-001)")
    # 28 x 47 g / 100 = 13.16
    assert ingredient.glycemic_load == 13.2
    dish = nutrition_for(reg, SETTINGS, "test_roti", 1, "piece")
    # 23.33 g of carbohydrate at GI 50.
    assert dish.glycemic_load == 11.7


def test_glycemic_load_missing_without_a_gi(reg: Registry) -> None:
    result = nutrition_for(reg, SETTINGS, "test_dal", 100, "g")
    assert result.gi is None
    assert result.glycemic_load is None


def test_glycemic_load_missing_when_the_carbohydrate_sum_is_partial(reg: Registry) -> None:
    result = nutrition_for(reg, SETTINGS, "test_curry", 1, "serving")
    # The dish has a GI, but its carbohydrate is a floor, and a floor would understate the load.
    assert result.gi == 40
    assert result.incomplete["carbs_available_g"] == ["masala_blend"]
    assert result.glycemic_load is None


def test_default_katori_comes_from_settings(reg: Registry) -> None:
    result = nutrition_for(reg, Settings(default_katori_g=200.0), "test_curry", 1, "katori")
    assert result.grams == 200.0
    assert "default katori" in result.measure_assumption
    # A dish's own katori is never replaced by the default.
    own = nutrition_for(reg, Settings(default_katori_g=200.0), "test_dal", 1, "katori")
    assert own.grams == 150.0


def test_nutrition_for_summarises_the_food(reg: Registry) -> None:
    food = nutrition_for(reg, SETTINGS, "test_dal", 100, "g").food
    assert (food.id, food.kind, food.diet) == ("test_dal", "dish", "vegan")
    assert food.regions == ["south", "north"]
    assert food.aliases == ["plain dal"]
    assert food.measures == {"katori": 150.0, "serving": 200.0}


def test_nutrition_for_unknown_food(reg: Registry) -> None:
    with pytest.raises(UnknownFood):
        nutrition_for(reg, SETTINGS, "nope", 1, "g")


def test_nutrition_for_unit_error_lists_valid_units(reg: Registry) -> None:
    with pytest.raises(UnitError) as caught:
        nutrition_for(reg, SETTINGS, "toor_dal", 1, "katori")
    assert caught.value.valid_units == ["g"]


# meal_totals


def test_meal_totals_sum(reg: Registry) -> None:
    result = meal_totals(reg, SETTINGS, [item("test_dal", 200, "g"), item("test_roti", 2, "piece")])
    assert [i.food.id for i in result.items] == ["test_dal", "test_roti"]
    assert result.items[0].nutrients.kcal == 171.5
    # 171.5 + 226.667 kcal, and 11 + 8 g protein.
    assert result.total.kcal == pytest.approx(398.17)
    assert result.total.protein_g == pytest.approx(19.0)


def test_meal_total_adds_unrounded_item_values(reg: Registry) -> None:
    result = meal_totals(reg, SETTINGS, [item("test_roti", 1, "piece")] * 3)
    # Each item shows 113.33; adding the shown values would give 339.99.
    assert [i.nutrients.kcal for i in result.items] == [113.33] * 3
    assert result.total.kcal == pytest.approx(340.0)


def test_meal_glycemic_load_sums_the_known_items(reg: Registry) -> None:
    result = meal_totals(
        reg,
        SETTINGS,
        [item("toor_dal", 100, "g"), item("test_roti", 2, "piece"), item("test_dal", 100, "g")],
    )
    # 13.2 for the dal and 23.3 for the roti; the dish with no GI is named, not counted as 0.
    assert result.glycemic_load == 36.5
    assert result.glycemic_load_missing == ["test_dal"]


def test_meal_glycemic_load_missing_when_no_item_has_one(reg: Registry) -> None:
    result = meal_totals(reg, SETTINGS, [item("test_dal", 100, "g"), item("test_curry", 1, "g")])
    assert result.glycemic_load is None
    assert result.glycemic_load_missing == ["test_dal", "test_curry"]


def test_meal_incomplete_flagged(reg: Registry) -> None:
    result = meal_totals(
        reg, SETTINGS, [item("test_curry", 1, "serving"), item("test_roti", 1, "piece")]
    )
    # 2.6 mg from the curry (a floor, the blend has no iron value) and 1.17 mg from the roti.
    assert result.total.iron_mg == pytest.approx(3.77)
    assert result.total_incomplete["iron_mg"] == ["masala_blend"]
    assert result.items[0].incomplete["iron_mg"] == ["masala_blend"]
    # Every ingredient reports folate, so it is not flagged.
    assert "folate_ug" not in result.total_incomplete


def test_meal_incomplete_names_ingredients_once_in_order_of_appearance(reg: Registry) -> None:
    result = meal_totals(
        reg,
        SETTINGS,
        [item("masala_blend", 5, "g"), item("test_dal", 1, "serving"), item("test_curry", 1, "g")],
    )
    # The blend lacks vitamin D itself, and the two dishes name toor_dal and the blend again.
    assert result.total_incomplete["vit_d_ug"] == ["masala_blend", "toor_dal"]


def test_meal_total_is_missing_when_no_item_reports_it(reg: Registry) -> None:
    result = meal_totals(reg, SETTINGS, [item("test_dal", 1, "serving"), item("water", 100, "g")])
    assert result.total.vit_b12_ug is None
    assert result.total_incomplete["vit_b12_ug"] == ["toor_dal", "water"]


def test_meal_known_zero_is_not_incomplete(reg: Registry) -> None:
    result = meal_totals(reg, SETTINGS, [item("water", 100, "g")])
    assert result.total.vit_d_ug == 0
    assert "vit_d_ug" not in result.total_incomplete


def test_meal_bad_item_named(reg: Registry) -> None:
    with pytest.raises(ValueError, match=r"item 2 \(nope\): unknown food id 'nope'") as caught:
        meal_totals(reg, SETTINGS, [item("test_dal", 100, "g"), item("nope", 1, "g")])
    assert isinstance(caught.value.__cause__, UnknownFood)


def test_meal_bad_unit_named(reg: Registry) -> None:
    with pytest.raises(ValueError, match=r"item 1 \(toor_dal\): .*valid units: g") as caught:
        meal_totals(reg, SETTINGS, [item("toor_dal", 1, "katori")])
    assert isinstance(caught.value.__cause__, UnitError)


def test_meal_bad_amount_named(reg: Registry) -> None:
    with pytest.raises(ValueError, match=r"item 2 \(toor_dal\): 6000 g .* over the limit"):
        meal_totals(reg, SETTINGS, [item("test_dal", 1, "g"), item("toor_dal", 6000, "g")])


@pytest.mark.parametrize("amount", [0, -1])
def test_meal_item_amount_must_be_positive(amount: float) -> None:
    # The bound lives on the model so the compute_meal schema shows it.
    with pytest.raises(ValidationError, match="greater than 0"):
        item("toor_dal", amount, "g")


def test_meal_reports_the_first_bad_item(reg: Registry) -> None:
    with pytest.raises(ValueError, match=r"item 1 \(nope\)"):
        meal_totals(reg, SETTINGS, [item("nope", 1, "g"), item("also_nope", 1, "g")])


def test_meal_item_count_is_bounded(reg: Registry) -> None:
    with pytest.raises(ValueError, match="1 to 30 items"):
        meal_totals(reg, SETTINGS, [])
    with pytest.raises(ValueError, match="1 to 30 items"):
        meal_totals(reg, SETTINGS, [item("water", 1, "g")] * 31)
    assert len(meal_totals(reg, SETTINGS, [item("water", 1, "g")] * 30).items) == 30


# rank_foods


def test_rank_iron_per_100kcal_desc(reg: Registry) -> None:
    result = rank_foods(reg, "iron_mg", "100kcal", None, None, "ingredient", 10)
    assert (result.rank_by, result.per, result.order) == ("iron_mg", "100kcal", "desc")
    assert [r.food.id for r in result.results] == [
        "toor_dal",
        "wheat_flour",
        "egg_boiled",
        "chicken_raw",
        "ghee",
    ]
    assert [r.value for r in result.results] == [1.52, 1.03, 0.77, 0.61, 0.0]
    # Water has 0 kcal and the blend has no iron value.
    assert result.skipped_missing == 2


def test_rank_kcal_asc(reg: Registry) -> None:
    result = rank_foods(reg, "kcal", "100g", None, None, "ingredient", 3)
    assert result.order == "asc"
    assert [r.food.id for r in result.results] == ["water", "egg_boiled", "chicken_raw"]
    assert [r.value for r in result.results] == [0.0, 155.0, 165.0]
    assert result.skipped_missing == 0


def test_rank_skips_missing_and_counts(reg: Registry) -> None:
    result = rank_foods(reg, "iron_mg", "100g", None, None, None, 50)
    shown = {r.food.id for r in result.results}
    everything = {food.id for food in reg.foods()}
    # The blend has no iron value; the curry's iron is a floor because of the blend.
    assert everything - shown == {"masala_blend", "test_curry"}
    assert result.skipped_missing == 2


def test_rank_per_serving_ranks_dishes_only(reg: Registry) -> None:
    result = rank_foods(reg, "iron_mg", "serving", None, None, None, 10)
    assert [(r.food.id, r.value) for r in result.results] == [
        ("test_dal", 2.6),
        ("test_roti", 1.17),
    ]
    # Only the curry is counted; ingredients have no serving and are not "missing".
    assert result.skipped_missing == 1


def test_rank_per_serving_rejects_ingredients(reg: Registry) -> None:
    with pytest.raises(ValueError, match="dishes only"):
        rank_foods(reg, "iron_mg", "serving", None, None, "ingredient", 10)


def test_rank_kcal_per_100kcal_rejected(reg: Registry) -> None:
    with pytest.raises(ValueError, match="kcal"):
        rank_foods(reg, "kcal", "100kcal", None, None, None, 10)


def test_rank_by_must_be_a_nutrient_or_glycemic_load(reg: Registry) -> None:
    with pytest.raises(ValueError, match=r"valid values: .*iron_mg.*glycemic_load"):
        rank_foods(reg, "iron", "100g", None, None, None, 10)


def test_rank_per_must_be_known(reg: Registry) -> None:
    with pytest.raises(ValueError, match="valid values: 100kcal, 100g, serving"):
        rank_foods(reg, "iron_mg", "cup", None, None, None, 10)


@pytest.mark.parametrize("limit", [0, 51])
def test_rank_limit_is_bounded(reg: Registry, limit: int) -> None:
    with pytest.raises(ValueError, match="limit must be 1 to 50"):
        rank_foods(reg, "iron_mg", "100g", None, None, None, limit)


def test_rank_ties_are_broken_by_name(reg: Registry) -> None:
    result = rank_foods(reg, "protein_g", "100g", None, None, "ingredient", 10)
    assert [r.food.id for r in result.results] == [
        "chicken_raw",
        "toor_dal",
        "egg_boiled",
        "masala_blend",
        "wheat_flour",
        "ghee",
        "water",
    ]


def test_rank_glycemic_load_lowest_first(reg: Registry) -> None:
    result = rank_foods(reg, "glycemic_load", "100g", None, None, None, 10)
    assert result.order == "asc"
    assert [(r.food.id, r.value) for r in result.results] == [
        ("toor_dal", 13.2),
        ("test_roti", 29.2),
    ]
    # Eight foods have no GI or a partial carbohydrate sum (the curry).
    assert result.skipped_missing == 8


def test_rank_glycemic_load_per_serving_and_per_100kcal(reg: Registry) -> None:
    per_serving = rank_foods(reg, "glycemic_load", "serving", None, None, None, 10)
    assert [(r.food.id, r.value) for r in per_serving.results] == [("test_roti", 11.7)]
    assert per_serving.skipped_missing == 2
    per_kcal = rank_foods(reg, "glycemic_load", "100kcal", None, None, None, 10)
    assert [(r.food.id, r.value) for r in per_kcal.results] == [
        ("toor_dal", 3.8),
        ("test_roti", 10.3),
    ]


def test_rank_applies_the_filters(reg: Registry) -> None:
    result = rank_foods(reg, "protein_g", "100g", "north", "vegan", "dish", 10)
    assert [r.food.id for r in result.results] == ["test_roti", "test_dal"]


def test_rank_limit_keeps_the_count_of_skipped_foods(reg: Registry) -> None:
    result = rank_foods(reg, "iron_mg", "100g", None, None, None, 1)
    assert len(result.results) == 1
    assert result.skipped_missing == 2


# filter_foods


def filtered_ids(reg: Registry, region: str | None, diet: str | None, kind: str | None) -> set[str]:
    return {food.id for food in filter_foods(reg.foods(), region, diet, kind)}


def test_filter_without_filters_keeps_everything(reg: Registry) -> None:
    assert len(filter_foods(reg.foods(), None, None, None)) == len(reg.foods())


def test_diet_means_suitable_for(reg: Registry) -> None:
    vegan = filtered_ids(reg, None, "vegan", "ingredient")
    veg = filtered_ids(reg, None, "veg", "ingredient")
    egg = filtered_ids(reg, None, "egg", "ingredient")
    non_veg = filtered_ids(reg, None, "non_veg", "ingredient")
    assert "ghee" not in vegan
    assert {"ghee", "toor_dal"} <= veg
    assert "egg_boiled" not in veg
    assert "egg_boiled" in egg
    assert "chicken_raw" not in egg
    assert non_veg == {
        "toor_dal",
        "water",
        "ghee",
        "masala_blend",
        "wheat_flour",
        "egg_boiled",
        "chicken_raw",
    }


def test_region_filter_keeps_dishes_of_that_region(reg: Registry) -> None:
    assert filtered_ids(reg, "north", None, None) == {"test_dal", "test_roti"}
    assert filtered_ids(reg, "west", None, None) == {"test_curry"}
    # Valid, but nothing in the fixtures is tagged east.
    assert filtered_ids(reg, "east", None, None) == set()


def test_filters_combine(reg: Registry) -> None:
    assert filtered_ids(reg, "south", "veg", "dish") == {"test_dal"}


@pytest.mark.parametrize(
    ("region", "diet", "kind", "valid"),
    [
        ("mars", None, None, "north, south, east, west"),
        (None, "pescatarian", None, "vegan, veg, egg, non_veg"),
        (None, None, "meal", "ingredient, dish"),
    ],
)
def test_filter_rejects_unknown_values_and_lists_valid_ones(
    reg: Registry, region: str | None, diet: str | None, kind: str | None, valid: str
) -> None:
    with pytest.raises(ValueError, match=valid):
        filter_foods(reg.foods(), region, diet, kind)
