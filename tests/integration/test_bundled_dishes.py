import csv
import re
from collections import Counter
from pathlib import Path
from typing import get_args

from nutrition_mcp.loader import build_registry, bundled_dir, load_dataset
from nutrition_mcp.models import Diet, Dish, Ingredient, Region

MIN_DISHES_PER_REGION = 8
PORTION_MEASURES = {"katori", "piece", "roti", "plate", "glass"}

GI_SOURCE = "gi_compendium_2021"
GI_CSV = Path(__file__).parents[2] / "data_build" / "gi_compendium_india.csv"
# "gi_compendium_2021: Idli (Table 1, T1-698)", the format docs/data.md documents.
GI_REF = re.compile(
    r"^(?P<source>[a-z0-9_]+): (?P<food>.+) \(Table (?P<table>\d+), (?P<row_id>T\d+-\d{3})\)$"
)

# Diets from strictest to loosest. A dish's diet is the loosest diet among its ingredients,
# so a ghee tadka makes a dal veg, and an egg makes a curry egg.
DIET_ORDER: tuple[Diet, ...] = ("vegan", "veg", "egg", "non_veg")


def test_each_region_has_at_least_8_dishes() -> None:
    _, _, dishes = load_dataset(bundled_dir())
    counts = Counter(region for dish in dishes for region in dish.regions)
    short = {
        region: counts[region]
        for region in get_args(Region)
        if counts[region] < MIN_DISHES_PER_REGION
    }
    assert short == {}


def test_every_dish_has_a_portion_measure() -> None:
    _, _, dishes = load_dataset(bundled_dir())
    assert dishes
    lacking = [dish.id for dish in dishes if not PORTION_MEASURES & dish.measures.keys()]
    assert lacking == []


def test_dish_kcal_per_100g_is_plausible() -> None:
    # Building the registry computes each dish from its ingredient rows, so this also checks
    # that every recipe names ingredients that exist.
    dishes = [food for food in build_registry([]).foods() if food.kind == "dish"]
    assert dishes
    outside = {
        food.id: food.per_100g.kcal
        for food in dishes
        if food.per_100g.kcal is None or not 20 <= food.per_100g.kcal <= 600
    }
    assert outside == {}


def _term(text: str) -> str:
    # "toor_dal", "Toor dal" and "toor dal" are the same search term.
    return " ".join(text.replace("_", " ").lower().split())


def test_dish_names_do_not_name_another_food() -> None:
    # A dish alias that names an ingredient ("rajma") or another dish ("kadhi") sends a
    # search to the wrong food.
    _, ingredients, dishes = load_dataset(bundled_dir())
    assert dishes
    ingredient_terms = {
        _term(text)
        for ingredient in ingredients
        for text in (ingredient.id, ingredient.name, *ingredient.aliases)
    }
    owners: dict[str, set[str]] = {}
    clashes = []
    for dish in dishes:
        for text in (dish.id, dish.name, *dish.aliases):
            term = _term(text)
            owners.setdefault(term, set()).add(dish.id)
            if term in ingredient_terms:
                clashes.append(f"{dish.id}: '{text}' names an ingredient")
    clashes += [
        f"'{term}' is used by {', '.join(sorted(ids))}"
        for term, ids in owners.items()
        if len(ids) > 1
    ]
    assert clashes == []


def test_diet_tags_match_ingredients() -> None:
    _, ingredients, dishes = load_dataset(bundled_dir())
    assert dishes
    diet_of = {ingredient.id: ingredient.diet for ingredient in ingredients}
    wrong = {}
    for dish in dishes:
        loosest = max(
            (diet_of[item.ingredient_id] for item in dish.ingredients), key=DIET_ORDER.index
        )
        if dish.diet != loosest:
            wrong[dish.id] = f"tagged {dish.diet}, ingredients make it {loosest}"
    assert wrong == {}


def _gi_rows() -> dict[str, dict[str, str]]:
    with GI_CSV.open(encoding="utf-8", newline="") as handle:
        return {row["row_id"]: row for row in csv.DictReader(handle)}


def _foods_with_gi() -> list[Ingredient | Dish]:
    _, ingredients, dishes = load_dataset(bundled_dir())
    return [food for food in (*ingredients, *dishes) if food.gi_ref is not None]


def test_gi_refs_exist() -> None:
    # Each GI value must point at the compendium row it was copied from, with the same food
    # name, table and value, so a reader can check it against the paper.
    rows = _gi_rows()
    foods = _foods_with_gi()
    assert foods
    problems = []
    for food in foods:
        assert food.gi_ref is not None
        match = GI_REF.match(food.gi_ref)
        if match is None:
            problems.append(f"{food.id}: gi_ref {food.gi_ref!r} is not in the documented format")
            continue
        row = rows.get(match["row_id"])
        if row is None:
            problems.append(f"{food.id}: {match['row_id']} is not in {GI_CSV.name}")
            continue
        expected = (GI_SOURCE, row["food"], row["table"], float(row["gi"]))
        actual = (match["source"], match["food"], match["table"], food.gi)
        if actual != expected:
            problems.append(f"{food.id}: gi_ref and gi give {actual}, the row gives {expected}")
    assert problems == []


def test_gi_rows_in_use_carry_their_matching_notes() -> None:
    # The candidates considered for a match are recorded in the chosen row's notes (written
    # by scripts/extract_gi.py), so the notes and the gi_refs have to name the same pairs.
    used = {}
    for food in _foods_with_gi():
        assert food.gi_ref is not None
        match = GI_REF.match(food.gi_ref)
        assert match is not None
        used[match["row_id"]] = food.id
    noted = {
        row_id: found.group(1)
        for row_id, row in _gi_rows().items()
        if (found := re.search(r"Used for (\w+)\.", row["notes"]))
    }
    assert noted == used


def test_foods_with_gi_list_the_compendium_as_a_source() -> None:
    foods = [food for food in build_registry([]).foods() if food.gi is not None]
    assert foods
    assert [food.id for food in foods if GI_SOURCE not in food.source_ids] == []
