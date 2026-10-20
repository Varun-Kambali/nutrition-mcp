from collections import Counter
from typing import get_args

from nutrition_mcp.loader import build_registry, bundled_dir, load_dataset
from nutrition_mcp.models import Diet, Region

MIN_DISHES_PER_REGION = 8
PORTION_MEASURES = {"katori", "piece", "roti", "plate", "glass"}

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
