"""Build evals/datasets/references.csv, the reference values for the data-accuracy evaluation.

Usage: `uv run --group build-data python scripts/build_references.py`. USDA_FDC_API_KEY must be
set whenever a USDA record is not in .cache/usda/ yet (see .env.example). `--refresh` fetches
every USDA record again.

data_build/reference_map.csv names one independent record per bundled dish: a CoFID 2021
homemade or recipe dish, or a USDA FoodData Central survey food (FNDDS). The script fetches and
maps each record with the same code as scripts/build_ingredients.py, writes the values per
100 g and prints a review table with the values the server computes for the dish. Read every
row against the source description before committing: a reference has to be the same dish
with a similar preparation, and the notes column says what differs.
"""

import argparse
import csv
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
from build_ingredients import (
    SOURCE_IDS,
    BuildError,
    cofid_path,
    fetch_usda,
    format_number,
    load_cofid,
    usda_cache_path,
)

from nutrition_mcp.loader import build_registry, bundled_dir, load_dataset
from nutrition_mcp.models import Dish, Ingredient, Nutrients
from nutrition_mcp.nutrients import REFERENCE_NUTRIENTS
from nutrition_mcp.registry import Registry
from nutrition_mcp.source_mapping import cofid_to_nutrients, usda_to_nutrients

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = ROOT / "data_build" / "reference_map.csv"
OUT_PATH = ROOT / "evals" / "datasets" / "references.csv"

MAP_COLUMNS = ["dish_id", "source", "record", "notes"]
OUT_COLUMNS = ["dish_id", "source_id", "record", "description", *REFERENCE_NUTRIENTS]
# Survey foods describe a dish as eaten. SR Legacy and Foundation records are ingredients.
USDA_SURVEY = "Survey (FNDDS)"


@dataclass(frozen=True, slots=True)
class Reference:
    dish_id: str
    source_id: str
    record: str
    description: str
    per_100g: Nutrients


def read_reference_map(
    path: Path, dishes: Sequence[Dish], ingredients: Sequence[Ingredient]
) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != MAP_COLUMNS:
            raise BuildError(f"{path}: header must be {','.join(MAP_COLUMNS)}")
        rows = [{key: (value or "").strip() for key, value in row.items()} for row in reader]
    by_id = {dish.id: dish for dish in dishes}
    records = {
        ingredient.id: (ingredient.source_id, ingredient.source_record)
        for ingredient in ingredients
    }
    seen: set[str] = set()
    for line, row in enumerate(rows, start=2):
        dish = by_id.get(row["dish_id"])
        if dish is None:
            raise BuildError(f"{path}:{line}: {row['dish_id']!r} is not a bundled dish")
        # One reference per dish, so each dish counts once in the error averages.
        if dish.id in seen:
            raise BuildError(f"{path}:{line}: second reference for {dish.id!r}")
        seen.add(dish.id)
        if row["source"] not in SOURCE_IDS:
            raise BuildError(f"{path}:{line}: source must be one of {', '.join(SOURCE_IDS)}")
        # A reference that is one of the dish's own ingredient rows would compare the data
        # with itself.
        own = (SOURCE_IDS[row["source"]], row["record"])
        if any(records[item.ingredient_id] == own for item in dish.ingredients):
            raise BuildError(f"{path}:{line}: {row['record']} is an ingredient of {dish.id}")
    return rows


def write_csv(references: list[Reference], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(OUT_COLUMNS)
        for ref in references:
            values = [format_number(ref.per_100g.get(key)) for key in REFERENCE_NUTRIENTS]
            writer.writerow([ref.dish_id, ref.source_id, ref.record, ref.description, *values])


def print_review(references: list[Reference], registry: Registry) -> None:
    header = ("dish", "record", "reference description", "kcal", "protein", "carbs", "Fe", "Ca")
    rows = []
    for ref in references:
        ours = registry.get(ref.dish_id).per_100g

        def pair(key: str, ref: Reference = ref, ours: Nutrients = ours) -> str:
            # Reference first, then the computed dish value.
            reference = format_number(ref.per_100g.get(key), 1) or "-"
            return f"{reference} / {format_number(ours.get(key), 1)}"

        rows.append(
            (
                ref.dish_id,
                f"{ref.source_id} {ref.record}",
                ref.description,
                *(pair(key) for key in REFERENCE_NUTRIENTS),
            )
        )
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    print("Values per 100 g, reference / computed")
    for row in [header, *rows]:
        cells = (cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        print("  ".join(cells).rstrip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--refresh", action="store_true", help="fetch USDA records again")
    args = parser.parse_args(argv)

    _, ingredients, dishes = load_dataset(bundled_dir())
    rows = read_reference_map(MAP_PATH, dishes, ingredients)
    key = os.environ.get("USDA_FDC_API_KEY")
    uncached = [
        row["record"]
        for row in rows
        if row["source"] == "usda" and (args.refresh or not usda_cache_path(row["record"]).exists())
    ]
    if uncached and not key:
        print(
            f"USDA_FDC_API_KEY is not set and {len(uncached)} USDA records need fetching. "
            "See .env.example.",
            file=sys.stderr,
        )
        return 1
    cofid = load_cofid(cofid_path()) if any(row["source"] == "cofid" for row in rows) else {}

    references: list[Reference] = []
    # The key goes in a header, never the URL, so it cannot end up in logs or error messages.
    with httpx.Client(headers={"X-Api-Key": key or ""}, timeout=60) as client:
        for row in rows:
            if row["source"] == "usda":
                food = fetch_usda(client, row["record"], args.refresh)
                if food["dataType"] != USDA_SURVEY:
                    raise BuildError(
                        f"{row['dish_id']}: USDA {row['record']} is {food['dataType']}, "
                        f"expected {USDA_SURVEY}"
                    )
                nutrients = usda_to_nutrients(food)
                description = f"{food['description']} [{food['dataType']}]"
            else:
                if row["record"] not in cofid:
                    raise BuildError(f"{row['dish_id']}: CoFID code {row['record']} not found")
                cofid_row = cofid[row["record"]]
                nutrients = cofid_to_nutrients(cofid_row)
                # The Description column says how the dish was made or sampled.
                description = f"{cofid_row['Food Name']} ({cofid_row['Description']})"
            references.append(
                Reference(
                    row["dish_id"], SOURCE_IDS[row["source"]], row["record"], description, nutrients
                )
            )

    write_csv(references, OUT_PATH)
    print_review(references, build_registry([]))
    print(f"\nWrote {len(references)} references to {OUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)
