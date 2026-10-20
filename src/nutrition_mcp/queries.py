"""The queries behind the MCP tools: nutrition for an amount, meal totals, rankings, filters.

Sums and comparisons use unrounded values. Results are rounded to 2 decimals only when they
are built, so a total never carries the error of its rounded parts.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast, get_args

from nutrition_mcp.config import Settings
from nutrition_mcp.models import DIET_SUITABLE_FOR, Food, Kind, Nutrients, Region
from nutrition_mcp.nutrients import HEAT_SENSITIVE, NUTRIENT_KEYS, NUTRIENTS
from nutrition_mcp.nutrition import glycemic_load, scale, sum_nutrients
from nutrition_mcp.registry import Registry, UnknownFood
from nutrition_mcp.schemas import (
    DECIMALS,
    FoodSummary,
    MealItemIn,
    MealResult,
    NutritionResult,
    Per,
    RankedFood,
    RankResult,
)
from nutrition_mcp.units import to_grams

MAX_MEAL_ITEMS = 30
MAX_RANK_LIMIT = 50

RANK_BY: tuple[str, ...] = (*NUTRIENT_KEYS, "glycemic_load")
_LOWER_IS_BETTER = frozenset(spec.key for spec in NUTRIENTS if spec.lower_is_better)


def _rounded(nutrients: Nutrients) -> Nutrients:
    return Nutrients.model_validate(
        {key: None if value is None else round(value, DECIMALS) for key, value in nutrients}
    )


def _require_one_of(label: str, value: str | None, valid: Iterable[str]) -> None:
    options = list(valid)
    if value is not None and value not in options:
        raise ValueError(f"unknown {label} '{value}'; valid values: {', '.join(options)}")


def filter_foods(
    foods: Iterable[Food], region: str | None, diet: str | None, kind: str | None
) -> list[Food]:
    """Keep the foods that match every filter given; None means no filter.

    Region keeps dishes tagged with it, so it also leaves out ingredients, which have no
    region. Diet means suitable for: "veg" keeps vegan and vegetarian foods.
    """
    _require_one_of("region", region, get_args(Region))
    _require_one_of("diet", diet, DIET_SUITABLE_FOR)
    _require_one_of("kind", kind, get_args(Kind))
    suitable = DIET_SUITABLE_FOR[diet] if diet is not None else None
    return [
        food
        for food in foods
        if (region is None or region in food.regions)
        and (suitable is None or food.diet in suitable)
        and (kind is None or food.kind == kind)
    ]


def _raw_ingredient_totals(food: Food) -> list[str]:
    """The heat-sensitive nutrients a dish reports, which are raw-ingredient totals in v1."""
    if food.kind != "dish":
        return []
    return [
        key for key in NUTRIENT_KEYS if key in HEAT_SENSITIVE and food.per_100g.get(key) is not None
    ]


@dataclass(frozen=True, slots=True)
class _Portion:
    result: NutritionResult
    exact: Nutrients  # before rounding, which is what meal totals add up


def _portion(reg: Registry, settings: Settings, food_id: str, amount: float, unit: str) -> _Portion:
    food = reg.get(food_id)
    grams, assumption = to_grams(food, amount, unit, settings.default_katori_g)
    exact = scale(food.per_100g, grams)
    # A partial carbohydrate sum would understate the load, so the load is left out (ADR 0003).
    load = (
        None
        if "carbs_available_g" in food.incomplete
        else glycemic_load(food.gi, exact.carbs_available_g)
    )
    result = NutritionResult(
        food=FoodSummary.from_food(food),
        amount=amount,
        unit=unit,
        grams=round(grams, DECIMALS),
        measure_assumption=assumption,
        nutrients=_rounded(exact),
        incomplete=food.incomplete,
        raw_ingredient_totals=_raw_ingredient_totals(food),
        gi=food.gi,
        gi_ref=food.gi_ref,
        glycemic_load=load,
        oil_assumption=food.assumptions,
        sources=food.source_ids,
    )
    return _Portion(result, exact)


def nutrition_for(
    reg: Registry, settings: Settings, food_id: str, amount: float, unit: str
) -> NutritionResult:
    """Nutrients, glycemic load and the assumptions behind them for an amount of one food.

    Raises UnknownFood for an ID that is not loaded, UnitError for a unit that does not fit
    the food, and ValueError for an amount that is not positive or is over the limit.
    """
    return _portion(reg, settings, food_id, amount, unit).result


def _total_incomplete(
    portions: list[_Portion], lacking: dict[str, list[str]]
) -> dict[str, list[str]]:
    named: dict[str, list[str]] = {}
    for key in NUTRIENT_KEYS:
        names: list[str] = []
        for portion in portions:
            food_id = portion.result.food.id
            own = portion.result.incomplete.get(key)
            if own:
                # A dish names the ingredients it lacks.
                names.extend(own)
            elif food_id in lacking.get(key, ()):
                # An ingredient with no value for the nutrient names itself.
                names.append(food_id)
        if names:
            named[key] = list(dict.fromkeys(names))
    return named


def meal_totals(reg: Registry, settings: Settings, items: list[MealItemIn]) -> MealResult:
    """Per-item and total nutrients for a meal of 1 to 30 items.

    The first item that cannot be worked out raises ValueError naming it, with the original
    error as its cause, so no item is dropped quietly.
    """
    if not 1 <= len(items) <= MAX_MEAL_ITEMS:
        raise ValueError(f"a meal needs 1 to {MAX_MEAL_ITEMS} items, got {len(items)}")
    portions: list[_Portion] = []
    for number, item in enumerate(items, start=1):
        where = f"item {number} ({item.food_id})"
        try:
            portions.append(_portion(reg, settings, item.food_id, item.amount, item.unit))
        except UnknownFood as err:
            # KeyError would show the repr of the ID, so the message is written out.
            raise ValueError(f"{where}: unknown food id '{item.food_id}'") from err
        except ValueError as err:
            raise ValueError(f"{where}: {err}") from err

    total, lacking = sum_nutrients([(p.result.food.id, p.exact) for p in portions])
    loads = [p.result.glycemic_load for p in portions]
    known_loads = [load for load in loads if load is not None]
    return MealResult(
        items=[p.result for p in portions],
        total=_rounded(total),
        total_incomplete=_total_incomplete(portions, lacking),
        # Each load is already rounded to 1 decimal; this rounding only removes float noise.
        glycemic_load=round(sum(known_loads), 1) if known_loads else None,
        glycemic_load_missing=[
            p.result.food.id for p, load in zip(portions, loads, strict=True) if load is None
        ],
    )


def _basis_factor(food: Food, per: str) -> float | None:
    """What to multiply a per-100 g value by to get it per 100 g, per serving or per 100 kcal."""
    if per == "100g":
        return 1.0
    if per == "serving":
        return None if food.serving_g is None else food.serving_g / 100
    kcal = food.per_100g.kcal
    # A kcal figure that is missing, zero or partial gives no meaningful "per 100 kcal".
    if not kcal or "kcal" in food.incomplete:
        return None
    return 100 / kcal


def _ranked_value(food: Food, rank_by: str, per: str) -> float | None:
    factor = _basis_factor(food, per)
    if factor is None:
        return None
    nutrient = "carbs_available_g" if rank_by == "glycemic_load" else rank_by
    # A partial sum would rank the food on a number that understates it.
    if nutrient in food.incomplete:
        return None
    amount = food.per_100g.get(nutrient)
    if amount is None:
        return None
    if rank_by == "glycemic_load":
        return glycemic_load(food.gi, amount * factor)
    return amount * factor


def rank_foods(
    reg: Registry,
    rank_by: str,
    per: str,
    region: str | None,
    diet: str | None,
    kind: str | None,
    limit: int,
) -> RankResult:
    """Rank foods by a nutrient or by glycemic load, per 100 kcal, per 100 g or per serving.

    Highest first, except for nutrients where lower is better and for glycemic load. Foods
    with no usable value are left out and counted in `skipped_missing`. A per-serving ranking
    covers dishes only, and ingredients are not counted as skipped.
    """
    if rank_by not in RANK_BY:
        raise ValueError(f"unknown rank_by '{rank_by}'; valid values: {', '.join(RANK_BY)}")
    _require_one_of("per", per, get_args(Per))
    if not 1 <= limit <= MAX_RANK_LIMIT:
        raise ValueError(f"limit must be 1 to {MAX_RANK_LIMIT}, got {limit}")
    if rank_by == "kcal" and per == "100kcal":
        raise ValueError("kcal cannot be ranked per 100 kcal, because it is always 100")
    if per == "serving" and kind == "ingredient":
        raise ValueError("per 'serving' ranks dishes only, so it cannot be used with ingredients")

    foods = filter_foods(reg.foods(), region, diet, kind)
    if per == "serving":
        foods = [food for food in foods if food.kind == "dish"]

    ranked: list[tuple[Food, float]] = []
    skipped = 0
    for food in foods:
        value = _ranked_value(food, rank_by, per)
        if value is None:
            skipped += 1
        else:
            ranked.append((food, value))

    ascending = rank_by == "glycemic_load" or rank_by in _LOWER_IS_BETTER
    ranked.sort(key=lambda pair: (pair[1] if ascending else -pair[1], pair[0].name))
    return RankResult(
        rank_by=rank_by,
        per=cast(Per, per),
        order="asc" if ascending else "desc",
        results=[
            RankedFood(food=FoodSummary.from_food(food), value=round(value, DECIMALS))
            for food, value in ranked[:limit]
        ],
        skipped_missing=skipped,
    )
