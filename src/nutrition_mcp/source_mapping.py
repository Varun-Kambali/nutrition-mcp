"""Turns USDA FoodData Central records and CoFID 2021 rows into our per-100 g Nutrients.

Only scripts/build_ingredients.py uses this; the server reads the CSV it writes. The
definition differences between the two sources are written up in docs/data.md.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from nutrition_mcp.models import Nutrients
from nutrition_mcp.nutrients import NUTRIENTS

# USDA nutrient IDs per nutrient. When a record has more than one, the first ID listed wins.
# Foundation records often have no 1008 energy value, only the Atwater energy values 2048
# (specific factors) and 2047 (general factors). Foundation records report sugars as 1063.
USDA_IDS: dict[str, tuple[int, ...]] = {
    "kcal": (1008, 2048, 2047),
    "protein_g": (1003,),
    "fat_g": (1004,),
    "sat_fat_g": (1258,),
    "sugars_g": (2000, 1063),
    "fibre_g": (1079,),
    "sodium_mg": (1093,),
    "iron_mg": (1089,),
    "calcium_mg": (1087,),
    "zinc_mg": (1095,),
    "folate_ug": (1177,),
    "vit_a_ug": (1106,),
    "vit_b12_ug": (1178,),
    "vit_c_mg": (1162,),
    "vit_d_ug": (1114,),
}
USDA_CARBS_BY_DIFFERENCE = 1005
USDA_FIBRE = 1079

# CoFID reports available carbohydrate directly, so it needs no subtraction.
COFID_COLUMNS: dict[str, str] = {
    "kcal": "Energy (kcal) (kcal)",
    "protein_g": "Protein (g)",
    "fat_g": "Fat (g)",
    "sat_fat_g": "Satd FA /100g fd (g)",
    "carbs_available_g": "Carbohydrate (g)",
    "sugars_g": "Total sugars (g)",
    "fibre_g": "AOAC fibre (g)",
    "sodium_mg": "Sodium (mg)",
    "iron_mg": "Iron (mg)",
    "calcium_mg": "Calcium (mg)",
    "zinc_mg": "Zinc (mg)",
    "folate_ug": "Folate (µg)",
    "vit_a_ug": "Retinol Equivalent (µg)",
    "vit_b12_ug": "Vitamin B12 (µg)",
    "vit_c_mg": "Vitamin C (mg)",
    "vit_d_ug": "Vitamin D (µg)",
}

# Household measures in millilitres, used to read a density off a USDA portion.
ML_PER_CUP = 236.588
ML_PER_TBSP = 14.787
_VOLUME_UNITS: dict[str, str] = {"cup": "cup", "tbsp": "tbsp", "tablespoon": "tbsp"}
_ML = {"cup": ML_PER_CUP, "tbsp": ML_PER_TBSP}

_UNITS = {spec.key: spec.unit for spec in NUTRIENTS}


def _same_unit(usda_unit: str, ours: str) -> bool:
    # The /food endpoint writes "µg" and the search endpoint "UG", so compare without case
    # and treat "ug" as "µg".
    normalise = {"ug": "µg"}
    unit = usda_unit.strip().lower()
    return normalise.get(unit, unit) == ours.lower()


def _usda_amounts(food: Mapping[str, Any]) -> dict[int, tuple[float, str]]:
    """Amount and unit per nutrient ID. Group headings such as "Proximates" have no amount."""
    amounts: dict[int, tuple[float, str]] = {}
    for entry in food.get("foodNutrients", []):
        nutrient = entry.get("nutrient", {})
        if "id" in nutrient and entry.get("amount") is not None:
            amounts.setdefault(nutrient["id"], (float(entry["amount"]), nutrient["unitName"]))
    return amounts


def _first(amounts: Mapping[int, tuple[float, str]], ids: Iterable[int], key: str) -> float | None:
    for nutrient_id in ids:
        if nutrient_id in amounts:
            amount, unit = amounts[nutrient_id]
            if not _same_unit(unit, _UNITS[key]):
                raise ValueError(
                    f"{key}: USDA nutrient {nutrient_id} is in {unit}, expected {_UNITS[key]}"
                )
            return amount
    return None


def usda_to_nutrients(food: Mapping[str, Any]) -> Nutrients:
    """Map one record from the FDC /food/{fdcId} endpoint. Nutrients it lacks stay None."""
    amounts = _usda_amounts(food)
    values: dict[str, float | None] = {
        key: _first(amounts, ids, key) for key, ids in USDA_IDS.items()
    }
    # USDA carbohydrate is by difference and includes fibre; we store available carbohydrate.
    by_difference = _first(amounts, (USDA_CARBS_BY_DIFFERENCE,), "carbs_available_g")
    fibre = values["fibre_g"]
    values["carbs_available_g"] = (
        None if by_difference is None or fibre is None else max(0.0, by_difference - fibre)
    )
    return Nutrients.model_validate(values)


def _volume_unit(portion: Mapping[str, Any]) -> str | None:
    """The unit of a cup ("cup") or tablespoon ("tbsp") portion, None for any other portion.

    Foundation records name the unit in measureUnit. SR Legacy records set measureUnit to
    "undetermined" and put the unit in modifier, which must be the bare word: a modifier such
    as "cup, chopped" or "cup, whipped" describes a different state of the food.
    """
    unit = str(portion.get("measureUnit", {}).get("name", "")).strip().lower()
    if unit == "undetermined":
        unit = str(portion.get("modifier", "")).strip().lower()
    return _VOLUME_UNITS.get(unit)


def usda_density_g_per_ml(food: Mapping[str, Any]) -> float | None:
    """Grams per ml from the record's cup portion, else its tablespoon portion, else None.

    A cup is preferred because its gram weight carries less rounding than a tablespoon's.
    """
    densities: dict[str, float] = {}
    for portion in food.get("foodPortions", []):
        unit = _volume_unit(portion)
        if unit and portion.get("amount") and portion.get("gramWeight"):
            ml = float(portion["amount"]) * _ML[unit]
            densities.setdefault(unit, float(portion["gramWeight"]) / ml)
    return densities.get("cup", densities.get("tbsp"))


def parse_cofid_value(raw: str | float | None) -> float | None:
    """CoFID cell to a number: "Tr" (trace) is 0, "N" (not measured) and blanks are None."""
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw)
    text = raw.strip()
    if text == "Tr":
        return 0.0
    if text in ("N", ""):
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"unrecognised CoFID value {raw!r}") from None


def cofid_to_nutrients(row: Mapping[str, str | float | None]) -> Nutrients:
    """Map one CoFID food, with its proximates, inorganics and vitamins joined into one row.

    Raises KeyError if a column is absent, so a renamed column stops the build.
    """
    return Nutrients.model_validate(
        {key: parse_cofid_value(row[column]) for key, column in COFID_COLUMNS.items()}
    )
