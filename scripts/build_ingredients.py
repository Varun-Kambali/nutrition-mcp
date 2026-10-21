"""Build the bundled ingredients.csv from USDA FoodData Central and CoFID 2021.

Usage: `make data`. USDA_FDC_API_KEY must be set (see .env.example) whenever a USDA record
is not in .cache/usda/ yet. To fetch every USDA record again, run
`uv run --group build-data python scripts/build_ingredients.py --refresh`.

data_build/ingredient_map.csv names one source record per ingredient. The script fetches each
USDA record, downloads the CoFID 2021 spreadsheet once into .cache/, maps both onto our
nutrients with nutrition_mcp.source_mapping, writes src/nutrition_mcp/data/bundled/
ingredients.csv and prints a review table. Read every row of the table against the source
description (the food, raw or cooked, whole or split) before committing the CSV, because a
search hit can be the wrong food.
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import openpyxl

from nutrition_mcp.loader import INGREDIENT_COLUMNS
from nutrition_mcp.models import Ingredient, Nutrients
from nutrition_mcp.source_mapping import (
    COFID_COLUMNS,
    cofid_to_nutrients,
    usda_density_g_per_ml,
    usda_to_nutrients,
)

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = ROOT / "data_build" / "ingredient_map.csv"
OUT_PATH = ROOT / "src" / "nutrition_mcp" / "data" / "bundled" / "ingredients.csv"
CACHE = ROOT / ".cache"

USDA_FOOD_URL = "https://api.nal.usda.gov/fdc/v1/food/{fdc_id}"
COFID_URL = (
    "https://assets.publishing.service.gov.uk/media/60538b91e90e07527df82ae4/"
    "McCance_Widdowsons_Composition_of_Foods_Integrated_Dataset_2021..xlsx"
)
# The file as downloaded on 2026-10-19. A different hash means gov.uk replaced the file, and
# every CoFID row needs a fresh review before the hash here is updated.
COFID_SHA256 = "436e9445ef2adb2a75f3d7edd51302de3adad25385f9795fc94ba58bd030e97d"
COFID_SHEETS = ("1.3 Proximates", "1.4 Inorganics", "1.5 Vitamins")
# Each sheet has a header row, then two rows of column codes and descriptions, then data.
COFID_FIRST_DATA_ROW = 4

SOURCE_IDS = {"usda": "usda_fdc", "cofid": "cofid_2021"}
MAP_COLUMNS = ["id", "name", "aliases", "diet", "source", "record", "notes"]

# Ingredients poured or spooned as liquids in Indian kitchens. Only these get a density, and
# only when their USDA record has a cup or tablespoon portion to take it from.
LIQUIDS = frozenset(
    {
        "coconut_milk",
        "coconut_oil",
        "ghee",
        "lemon_juice",
        "mustard_oil",
        "sunflower_oil",
        "whole_milk",
    }
)


class BuildError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Built:
    ingredient: Ingredient
    source: str
    description: str


def read_map(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != MAP_COLUMNS:
            raise BuildError(f"{path}: header must be {','.join(MAP_COLUMNS)}")
        rows = [{key: (value or "").strip() for key, value in row.items()} for row in reader]
    seen: set[str] = set()
    for line, row in enumerate(rows, start=2):
        if row["id"] in seen:
            raise BuildError(f"{path}:{line}: duplicate id {row['id']!r}")
        seen.add(row["id"])
        if row["source"] not in SOURCE_IDS:
            raise BuildError(f"{path}:{line}: source must be one of {', '.join(SOURCE_IDS)}")
    unknown = LIQUIDS - seen
    if unknown:
        raise BuildError(f"LIQUIDS names ids not in the map: {', '.join(sorted(unknown))}")
    return rows


def _retrying_get(client: httpx.Client, url: str) -> httpx.Response:
    # The FDC API answers 503 now and then; a short wait is usually enough.
    for attempt in range(4):
        try:
            response = client.get(url)
        except httpx.TransportError:
            if attempt == 3:
                raise
        else:
            if response.status_code < 500 or attempt == 3:
                response.raise_for_status()
                return response
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def usda_cache_path(fdc_id: str) -> Path:
    return CACHE / "usda" / f"{fdc_id}.json"


def fetch_usda(client: httpx.Client, fdc_id: str, refresh: bool) -> dict[str, Any]:
    cached = usda_cache_path(fdc_id)
    if cached.exists() and not refresh:
        data: dict[str, Any] = json.loads(cached.read_text(encoding="utf-8"))
    else:
        response = _retrying_get(client, USDA_FOOD_URL.format(fdc_id=fdc_id))
        data = response.json()
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(response.text, encoding="utf-8")
    if str(data.get("fdcId")) != fdc_id:
        raise BuildError(f"{cached}: holds FDC ID {data.get('fdcId')}, expected {fdc_id}")
    return data


def cofid_path() -> Path:
    path = CACHE / "cofid_2021.xlsx"
    if not path.exists():
        print(f"Downloading CoFID 2021 to {path}", file=sys.stderr)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        with httpx.stream("GET", COFID_URL, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        partial.rename(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != COFID_SHA256:
        raise BuildError(f"{path}: SHA-256 is {digest}, expected {COFID_SHA256}")
    return path


def _sheet_rows(sheet: Any) -> Iterator[tuple[list[str], tuple[Any, ...]]]:
    rows = sheet.iter_rows(values_only=True)
    header = [str(cell).strip() if cell is not None else "" for cell in next(rows)]
    for number, row in enumerate(rows, start=2):
        if number >= COFID_FIRST_DATA_ROW and row[0] is not None:
            yield header, row


def load_cofid(path: Path) -> dict[str, dict[str, Any]]:
    """Food code to one row holding the proximates, inorganics and vitamins columns.

    The sheets are joined on column 0, the food code, whose header cell is blank in
    "1.4 Inorganics". A code that appears twice in a sheet is kept out of the result, so
    mapping an ingredient to it fails instead of picking one of the two foods.
    """
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    joined: dict[str, dict[str, Any]] = {}
    repeated: set[str] = set()
    try:
        for name in COFID_SHEETS:
            seen: set[str] = set()
            for header, row in _sheet_rows(workbook[name]):
                code = str(row[0]).strip()
                if code in seen:
                    repeated.add(code)
                seen.add(code)
                merged = joined.setdefault(code, {})
                for column, value in zip(header[1:], row[1:], strict=False):
                    if column:
                        merged.setdefault(column, value)
    finally:
        workbook.close()
    columns = {column for row in joined.values() for column in row}
    absent = [column for column in COFID_COLUMNS.values() if column not in columns]
    if absent:
        raise BuildError(f"{path}: columns not found: {', '.join(absent)}")
    for code in repeated:
        del joined[code]
    return joined


def build_row(
    row: Mapping[str, str],
    usda: Mapping[str, Any] | None,
    cofid: Mapping[str, Any] | None,
) -> Built:
    density: float | None = None
    nutrients: Nutrients
    if usda is not None:
        nutrients = usda_to_nutrients(usda)
        description = f"{usda['description']} [{usda['dataType']}]"
        if row["id"] in LIQUIDS:
            density = usda_density_g_per_ml(usda)
    elif cofid is not None:
        nutrients = cofid_to_nutrients(cofid)
        # CoFID's Description column carries the form ("Split peas, no seed coat"), which
        # is what the review has to check.
        description = f"{cofid['Food Name']} ({cofid['Description']})"
    else:
        raise BuildError(f"{row['id']}: no source record")
    # Validating with the dataset model means the written CSV is known to load.
    ingredient = Ingredient.model_validate(
        {
            "id": row["id"],
            "name": row["name"],
            "aliases": [alias.strip() for alias in row["aliases"].split("|") if alias.strip()],
            "diet": row["diet"],
            "source_id": SOURCE_IDS[row["source"]],
            "source_record": row["record"],
            "per_100g": nutrients,
            "density_g_per_ml": density,
        }
    )
    return Built(ingredient, row["source"], description)


def format_number(value: float | None, places: int = 4) -> str:
    # Fixed places, then trailing zeros dropped: 47.779999 -> "47.78", 328.0 -> "328".
    if value is None:
        return ""
    text = f"{round(value, places):.{places}f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def write_csv(built: list[Built], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(INGREDIENT_COLUMNS)
        for item in built:
            ingredient = item.ingredient
            cells = {
                "id": ingredient.id,
                "name": ingredient.name,
                "aliases": "|".join(ingredient.aliases),
                "diet": ingredient.diet,
                "source_id": ingredient.source_id,
                "source_record": ingredient.source_record,
                "gi": "",
                "gi_ref": "",
                "density_g_per_ml": format_number(ingredient.density_g_per_ml),
            }
            for key, value in ingredient.per_100g:
                cells[key] = format_number(value)
            writer.writerow([cells[column] for column in INGREDIENT_COLUMNS])


def print_review(built: list[Built]) -> None:
    header = ("our name", "source", "record", "source description", "kcal", "protein", "gaps")
    rows = []
    for item in built:
        values = item.ingredient.per_100g
        gaps = sum(1 for _, value in values if value is None)
        rows.append(
            (
                item.ingredient.name,
                item.source,
                item.ingredient.source_record,
                item.description,
                format_number(values.kcal, 1),
                format_number(values.protein_g, 1),
                str(gaps),
            )
        )
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        cells = (cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        print("  ".join(cells).rstrip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--refresh", action="store_true", help="fetch USDA records again")
    args = parser.parse_args(argv)

    rows = read_map(MAP_PATH)
    key = os.environ.get("USDA_FDC_API_KEY")
    # Cached records rebuild the table offline; only a fetch needs the key.
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

    built: list[Built] = []
    publications: dict[str, set[str]] = {}
    # The key goes in a header, never the URL, so it cannot end up in logs or error messages.
    with httpx.Client(headers={"X-Api-Key": key or ""}, timeout=60) as client:
        for row in rows:
            if row["source"] == "usda":
                food = fetch_usda(client, row["record"], args.refresh)
                publications.setdefault(food["dataType"], set()).add(food["publicationDate"])
                built.append(build_row(row, food, None))
            else:
                if row["record"] not in cofid:
                    raise BuildError(f"{row['id']}: CoFID code {row['record']} not found")
                built.append(build_row(row, None, cofid[row["record"]]))

    write_csv(built, OUT_PATH)
    print_review(built)
    print(f"\nWrote {len(built)} ingredients to {OUT_PATH.relative_to(ROOT)}")
    for data_type, dates in sorted(publications.items()):
        print(f"USDA {data_type} publication dates: {', '.join(sorted(dates))}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)
