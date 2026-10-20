"""Reads dataset folders from disk, validates them and resolves dishes into Food records.

A dataset folder holds sources.yaml, ingredients.csv and dishes/*.yaml. Every problem in
those files raises DataError with the file and row, so a bad dataset stops startup with a
message the author can act on.
"""

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from nutrition_mcp.models import Dish, Food, Ingredient, Nutrients, Source
from nutrition_mcp.nutrients import NUTRIENT_KEYS
from nutrition_mcp.registry import Registry

# ingredients.csv is flat, so the nested per_100g model becomes one column per nutrient.
INGREDIENT_COLUMNS: tuple[str, ...] = tuple(
    column
    for name in Ingredient.model_fields
    for column in (NUTRIENT_KEYS if name == "per_100g" else (name,))
)
# Nutrient, GI and density columns may be left out of the file; a missing value stays None.
REQUIRED_COLUMNS: tuple[str, ...] = tuple(
    name
    for name, field in Ingredient.model_fields.items()
    if field.is_required() and name != "per_100g"
)


class DataError(Exception):
    """A problem in a dataset file. The message starts with "<path>:<row>: ".

    For ingredients.csv the row is the file line, with the header on line 1. It is 0 for
    problems with a whole file, which includes every dish file and sources.yaml entry.
    """

    def __init__(self, path: Path, row: int, message: str) -> None:
        super().__init__(f"{path}:{row}: {message}")


@dataclass(frozen=True, slots=True)
class _Located[T]:
    """A loaded record with the place it came from, for errors raised after loading."""

    item: T
    path: Path
    row: int

    def error(self, message: str) -> DataError:
        return DataError(self.path, self.row, message)


@dataclass(frozen=True, slots=True)
class _Dataset:
    sources: list[_Located[Source]]
    ingredients: list[_Located[Ingredient]]
    dishes: list[_Located[Dish]]


def bundled_dir() -> Path:
    return Path(str(resources.files("nutrition_mcp") / "data" / "bundled"))


def _read_text(path: Path, *, newline: str | None = None) -> str:
    # utf-8-sig drops the byte order mark that Excel puts at the start of a CSV.
    try:
        with path.open(encoding="utf-8-sig", newline=newline) as handle:
            return handle.read()
    except OSError as err:
        raise DataError(path, 0, f"cannot read file: {err.strerror or err}") from err
    except UnicodeDecodeError as err:
        raise DataError(path, 0, "file is not valid UTF-8") from err


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(_read_text(path))
    except (yaml.YAMLError, ValueError) as err:
        # PyYAML raises a plain ValueError, with no position, for a date that matches the
        # format but does not exist (2026-13-45), so it needs the same file-level report.
        mark = getattr(err, "problem_mark", None)
        problem = getattr(err, "problem", None) or str(err)
        raise DataError(path, mark.line + 1 if mark else 0, f"invalid YAML: {problem}") from err


def _describe(error: ValidationError) -> str:
    parts = []
    for detail in error.errors():
        field = ".".join(str(part) for part in detail["loc"])
        parts.append(f"{field}: {detail['msg']}" if field else detail["msg"])
    return "; ".join(parts)


def _validate[M: BaseModel](
    model: type[M], data: object, path: Path, row: int, prefix: str = ""
) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as err:
        raise DataError(path, row, f"{prefix}{_describe(err)}") from err


def _load_sources(path: Path) -> list[_Located[Source]]:
    entries = _read_yaml(path)
    if not isinstance(entries, list):
        raise DataError(path, 0, "expected a list of sources")
    return [
        _Located(_validate(Source, entry, path, 0, f"entry {number}: "), path, 0)
        for number, entry in enumerate(entries, start=1)
    ]


def _ingredient_from_cells(cells: dict[str, str | None], path: Path, line: int) -> Ingredient:
    data: dict[str, Any] = {
        column: value for column, value in cells.items() if column not in NUTRIENT_KEYS
    }
    aliases = data.get("aliases")
    data["aliases"] = [a.strip() for a in aliases.split("|") if a.strip()] if aliases else []
    data["per_100g"] = {key: cells.get(key) for key in NUTRIENT_KEYS}
    return _validate(Ingredient, data, path, line)


def _load_ingredients(path: Path) -> list[_Located[Ingredient]]:
    reader = csv.DictReader(io.StringIO(_read_text(path, newline=""), newline=""))
    header = reader.fieldnames
    if not header:
        raise DataError(path, 0, "file is empty, expected a header row")
    unknown = [column for column in header if column not in INGREDIENT_COLUMNS]
    if unknown:
        raise DataError(
            path,
            1,
            f"unknown column(s): {', '.join(unknown)}. Allowed: {', '.join(INGREDIENT_COLUMNS)}",
        )
    repeated = sorted({column for column in header if header.count(column) > 1})
    if repeated:
        raise DataError(path, 1, f"repeated column(s): {', '.join(repeated)}")
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise DataError(path, 1, f"missing required column(s): {', '.join(missing)}")

    ingredients = []
    for row in reader:
        # line_num is the last line of the record, which is its only line unless a cell
        # holds a quoted line break.
        line = reader.line_num
        if None in row or None in row.values():
            raise DataError(path, line, f"expected {len(header)} cells")
        cells = {column: row[column].strip() or None for column in header}
        ingredients.append(_Located(_ingredient_from_cells(cells, path, line), path, line))
    return ingredients


def _load_dishes(folder: Path) -> list[_Located[Dish]]:
    dishes_dir = folder / "dishes"
    # Without this check a misspelt folder name would load as a dataset with no dishes.
    if not dishes_dir.is_dir():
        raise DataError(dishes_dir, 0, "folder not found")
    return [
        _Located(_validate(Dish, _read_yaml(path), path, 0), path, 0)
        for path in sorted(dishes_dir.glob("*.yaml"))
    ]


def _read_dataset(folder: Path) -> _Dataset:
    return _Dataset(
        sources=_load_sources(folder / "sources.yaml"),
        ingredients=_load_ingredients(folder / "ingredients.csv"),
        dishes=_load_dishes(folder),
    )


def load_dataset(folder: Path) -> tuple[list[Source], list[Ingredient], list[Dish]]:
    """Read and validate one folder. Checks that span folders happen in build_registry."""
    data = _read_dataset(folder)
    return (
        [located.item for located in data.sources],
        [located.item for located in data.ingredients],
        [located.item for located in data.dishes],
    )


def compute_dish(
    dish: Dish, ingredients: Mapping[str, Ingredient]
) -> tuple[Nutrients, dict[str, list[str]]]:
    """Per-100 g nutrients of a dish and, per nutrient, the ingredients that lack it.

    Each value sums the ingredients that report the nutrient, so with a gap it is a floor and
    not a total; the incomplete mapping says which nutrients that applies to. A nutrient no
    ingredient reports is None. Raises KeyError for an ingredient ID not in `ingredients`.
    """
    items = [(ingredients[item.ingredient_id], item) for item in dish.ingredients]
    values: dict[str, float | None] = {}
    incomplete: dict[str, list[str]] = {}
    for key in NUTRIENT_KEYS:
        known: list[float] = []
        missing: list[str] = []
        for ingredient, item in items:
            amount = ingredient.per_100g.get(key)
            if amount is None:
                missing.append(item.ingredient_id)
            else:
                known.append(amount * item.grams / 100)
        values[key] = sum(known) / dish.cooked_weight_g * 100 if known else None
        if missing:
            # A dish can list one ingredient twice, but it only needs naming once.
            incomplete[key] = list(dict.fromkeys(missing))
    return Nutrients.model_validate(values), incomplete


def _gi_source_id(gi_ref: str | None) -> str | None:
    """The source ID a gi_ref starts with: "gi_compendium_2021: Idli (Table 1, T1-698)".

    None when there is no gi_ref, or when it does not start with an ID and a colon.
    """
    if gi_ref is None:
        return None
    source_id, colon, _ = gi_ref.partition(":")
    if not colon:
        return None
    return source_id.strip() or None


def _source_ids(*ids: str | None) -> list[str]:
    # Each source once, in the order given. A food without a GI passes None for it.
    return list(dict.fromkeys(source_id for source_id in ids if source_id is not None))


def _ingredient_food(ingredient: Ingredient) -> Food:
    return Food(
        id=ingredient.id,
        kind="ingredient",
        name=ingredient.name,
        aliases=ingredient.aliases,
        regions=[],
        diet=ingredient.diet,
        source_ids=_source_ids(ingredient.source_id, _gi_source_id(ingredient.gi_ref)),
        per_100g=ingredient.per_100g,
        incomplete={},
        gi=ingredient.gi,
        gi_ref=ingredient.gi_ref,
        measures={},
        serving_g=None,
        assumptions=None,
        density_g_per_ml=ingredient.density_g_per_ml,
    )


def _dish_food(
    dish: Dish,
    per_100g: Nutrients,
    incomplete: dict[str, list[str]],
    ingredients: Mapping[str, Ingredient],
) -> Food:
    serving_g = dish.cooked_weight_g / dish.servings
    measures = dict(dish.measures)
    measures.setdefault("serving", serving_g)
    ingredient_sources = (ingredients[item.ingredient_id].source_id for item in dish.ingredients)
    return Food(
        id=dish.id,
        kind="dish",
        name=dish.name,
        aliases=dish.aliases,
        regions=dish.regions,
        diet=dish.diet,
        source_ids=_source_ids(dish.source_id, *ingredient_sources, _gi_source_id(dish.gi_ref)),
        per_100g=per_100g,
        incomplete=incomplete,
        gi=dish.gi,
        gi_ref=dish.gi_ref,
        measures=measures,
        serving_g=serving_g,
        assumptions=dish.assumptions,
        density_g_per_ml=None,
    )


def _claim(seen: dict[str, _Located[Any]], located: _Located[Any], label: str) -> None:
    """Record an ID, or fail if an earlier record already uses it."""
    first = seen.setdefault(located.item.id, located)
    if first is not located:
        raise located.error(
            f"duplicate {label} '{located.item.id}', already used at {first.path}:{first.row}"
        )


def build_registry(folders: Sequence[Path]) -> Registry:
    """Load the bundled dataset, then each folder in order, into one registry.

    IDs must be unique across everything loaded: a clash fails here instead of letting one
    dataset quietly shadow another.
    """
    datasets = [_read_dataset(folder) for folder in (bundled_dir(), *folders)]

    sources: dict[str, _Located[Any]] = {}
    foods: dict[str, _Located[Any]] = {}
    for data in datasets:
        for source in data.sources:
            _claim(sources, source, "source id")
        for food in (*data.ingredients, *data.dishes):
            _claim(foods, food, "food id")

    for located in foods.values():
        if located.item.source_id not in sources:
            raise located.error(
                f"unknown source_id '{located.item.source_id}': list it in a sources.yaml"
            )
        # The GI usually comes from another source than the nutrients, so gi_ref names it,
        # and that source has to be loaded like any other.
        gi_ref = located.item.gi_ref
        if gi_ref is None:
            continue
        gi_source = _gi_source_id(gi_ref)
        if gi_source is None:
            raise located.error(
                f"gi_ref '{gi_ref}' must start with a source id and a colon, as in "
                "'gi_compendium_2021: Idli (Table 1, T1-698)'"
            )
        if gi_source not in sources:
            raise located.error(
                f"gi_ref names unknown source '{gi_source}': list it in a sources.yaml"
            )

    ingredients = {
        located.item.id: located.item for data in datasets for located in data.ingredients
    }
    registry_foods: dict[str, Food] = {}
    for data in datasets:
        for ingredient in data.ingredients:
            registry_foods[ingredient.item.id] = _ingredient_food(ingredient.item)
        for dish in data.dishes:
            try:
                per_100g, incomplete = compute_dish(dish.item, ingredients)
            except KeyError as err:
                raise dish.error(f"unknown ingredient '{err.args[0]}'") from err
            registry_foods[dish.item.id] = _dish_food(dish.item, per_100g, incomplete, ingredients)
    return Registry(registry_foods, {sid: located.item for sid, located in sources.items()})
