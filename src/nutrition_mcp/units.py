"""Turn an amount and a unit into grams for one food.

A food's own measures are tried first and the defaults second, so a dish that defines its own
katori never gets the 150 g default, and a food that defines a tablespoon in grams never gets
15 ml times its density.
"""

from dataclasses import dataclass

from nutrition_mcp.models import Food

MAX_GRAMS = 5000.0

# Millilitres in one unit. They only apply to a food with a density, because grams need it.
_VOLUME_ML = {"ml": 1.0, "tbsp": 15.0, "tsp": 5.0, "cup": 240.0, "glass": 250.0}

_GRAM_SPELLINGS = frozenset({"gram", "grams", "gm"})

# A dish that defines one of these is counted, not ladled, so it gets no default katori.
_COUNTED_MEASURES = frozenset({"piece", "roti"})


class UnitError(ValueError):
    """The unit does not fit the food. `valid_units` is what the caller can use instead."""

    def __init__(self, message: str, valid_units: list[str]) -> None:
        super().__init__(message)
        self.valid_units = valid_units


@dataclass(frozen=True)
class _Unit:
    grams: float  # grams in one of this unit
    note: str = ""  # how the number was reached, when it is not the food's own measure


def normalise_unit(unit: str) -> str:
    cleaned = unit.strip().lower()
    return "g" if cleaned in _GRAM_SPELLINGS else cleaned


def _unit_table(food: Food, default_katori_g: float) -> dict[str, _Unit]:
    """Every unit this food accepts, in the order `valid_units` reports them.

    Own measures go in before any default, and `setdefault` keeps the first entry, so a food's
    own number always wins.
    """
    table = {"g": _Unit(1.0)}
    for name, grams in food.measures.items():
        table.setdefault(name, _Unit(grams))
    # A bowl size means nothing for a dish counted by the piece, so "a katori of roti" is an
    # error unless the dish defines its own katori (which the measures loop already added).
    if food.kind == "dish" and not _COUNTED_MEASURES & food.measures.keys():
        table.setdefault("katori", _Unit(default_katori_g, "(default katori)"))
    density = food.density_g_per_ml
    if density is not None:
        for name, ml in _VOLUME_ML.items():
            note = (
                f"at {density:g} g/ml" if name == "ml" else f"({ml:g} ml each at {density:g} g/ml)"
            )
            table.setdefault(name, _Unit(ml * density, note))
    return table


def valid_units(food: Food, default_katori_g: float) -> list[str]:
    return list(_unit_table(food, default_katori_g))


def unit_grams(food: Food, default_katori_g: float) -> dict[str, float]:
    """Grams in one of each unit the food accepts, in the order `valid_units` gives them."""
    return {name: unit.grams for name, unit in _unit_table(food, default_katori_g).items()}


def _find_unit(table: dict[str, _Unit], unit: str) -> str | None:
    # Try the word as given, then as a plural: "rotis", "pieces" and "glasses" all need a
    # different suffix removed, and a food that defines "pieces" itself should still win.
    word = normalise_unit(unit)
    for candidate in (word, word.removesuffix("s"), word.removesuffix("es")):
        if candidate in table:
            return candidate
    return None


def _show_grams(grams: float) -> str:
    return f"{round(grams, 1):g}"


def to_grams(food: Food, amount: float, unit: str, default_katori_g: float) -> tuple[float, str]:
    """Return the grams for `amount` of `unit`, and a short note on how they were worked out."""
    # Written as "not >" so that NaN is rejected along with zero and negatives.
    if not amount > 0:
        raise ValueError(f"amount must be greater than 0, got {amount:g}")

    table = _unit_table(food, default_katori_g)
    name = _find_unit(table, unit)
    if name is None:
        units = list(table)
        raise UnitError(
            f"unknown unit '{unit}' for {food.id}; valid units: {', '.join(units)}", units
        )

    entry = table[name]
    grams = amount * entry.grams
    if grams > MAX_GRAMS:
        raise ValueError(
            f"{amount:g} {name} comes to {_show_grams(grams)} g, over the limit of {MAX_GRAMS:g} g"
        )

    if name == "g":
        return grams, f"{_show_grams(grams)} g"
    assumption = f"{amount:g} {name} = {_show_grams(grams)} g"
    return grams, f"{assumption} {entry.note}" if entry.note else assumption
