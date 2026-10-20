"""Nutrition arithmetic. The model never does these sums; the tools call these functions."""


def glycemic_load(gi: float | None, carbs_available_g: float | None) -> float | None:
    """Glycemic load of an amount of food: GI x available carbohydrate (g) / 100.

    Rounded to 1 decimal, since GI values are study averages and a second decimal would
    claim precision they do not have. None when either input is missing; a missing GI is
    never treated as 0.
    """
    if gi is None or carbs_available_g is None:
        return None
    return round(gi * carbs_available_g / 100, 1)
