"""Nutrition arithmetic. The model never does these sums; the tools call these functions."""

from nutrition_mcp.models import Nutrients
from nutrition_mcp.nutrients import NUTRIENT_KEYS


def glycemic_load(gi: float | None, carbs_available_g: float | None) -> float | None:
    """Glycemic load of an amount of food: GI x available carbohydrate (g) / 100.

    Rounded to 1 decimal, since GI values are study averages and a second decimal would
    claim precision they do not have. None when either input is missing; a missing GI is
    never treated as 0.
    """
    if gi is None or carbs_available_g is None:
        return None
    return round(gi * carbs_available_g / 100, 1)


def scale(per_100g: Nutrients, grams: float) -> Nutrients:
    """The nutrients in `grams` of a food from its per-100 g values. Missing stays missing."""
    return per_100g.scaled(grams / 100)


def sum_nutrients(
    parts: list[tuple[str, Nutrients]],
) -> tuple[Nutrients, dict[str, list[str]]]:
    """Add up labelled amounts of nutrients, and say which labels lack each one.

    A total counts the parts that report the nutrient, so with a gap it is a floor. It is
    None only when every part lacks it. The mapping names, for each nutrient, the labels
    that did not report it, each once.
    """
    totals: dict[str, float | None] = {}
    lacking: dict[str, list[str]] = {}
    for key in NUTRIENT_KEYS:
        known = [value for _, nutrients in parts if (value := nutrients.get(key)) is not None]
        totals[key] = sum(known) if known else None
        missing = [label for label, nutrients in parts if nutrients.get(key) is None]
        if missing:
            lacking[key] = list(dict.fromkeys(missing))
    return Nutrients.model_validate(totals), lacking
