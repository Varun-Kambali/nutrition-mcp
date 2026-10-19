"""Write the JSON Schemas for the three dataset files to schemas/.

Run `uv run python scripts/export_schemas.py` after changing nutrition_mcp/models.py or the
CSV columns in nutrition_mcp/loader.py, and commit the result.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from nutrition_mcp.loader import INGREDIENT_COLUMNS, REQUIRED_COLUMNS
from nutrition_mcp.models import Dish, Ingredient, Source
from nutrition_mcp.nutrients import NUTRIENTS

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"
DIALECT = "https://json-schema.org/draft/2020-12/schema"

# What the model fields cannot say: how the flat CSV spells them.
COLUMN_NOTES = {
    "aliases": "Other names and spellings, separated by '|'. Leave empty for none.",
    "source_id": "ID of an entry in a sources.yaml.",
    "source_record": "Record ID in the source, such as a USDA FDC ID or a CoFID food code.",
    "gi": "Glycemic index. Needs gi_ref.",
    "gi_ref": "The compendium's food name and table the GI came from. Needs gi.",
    "density_g_per_ml": "Grams per millilitre, for liquids.",
}


def ingredient_schema() -> dict[str, Any]:
    """One row of ingredients.csv: the header names are the keys, an empty cell is null."""
    fields = Ingredient.model_json_schema()["properties"]
    nutrients = {spec.key: spec for spec in NUTRIENTS}
    properties: dict[str, Any] = {}
    for column in INGREDIENT_COLUMNS:
        if column in nutrients:
            spec = nutrients[column]
            properties[column] = {
                "type": ["number", "null"],
                "minimum": 0,
                "description": (
                    f"{spec.label} in {spec.unit} per 100 g. Leave empty when the source "
                    "does not report it; empty is not the same as 0."
                ),
            }
        elif column == "aliases":
            properties[column] = {"type": "string", "description": COLUMN_NOTES[column]}
        else:
            properties[column] = dict(fields[column])
            if column in COLUMN_NOTES:
                properties[column]["description"] = COLUMN_NOTES[column]
    return {
        "$schema": DIALECT,
        "title": "Ingredient row",
        "description": (
            "One row of ingredients.csv in a dataset folder. Each column is one key; an empty "
            "cell means the value is not reported."
        ),
        "type": "object",
        "properties": properties,
        "required": list(REQUIRED_COLUMNS),
        "additionalProperties": False,
    }


def dish_schema() -> dict[str, Any]:
    return {
        "$schema": DIALECT,
        "description": "One dishes/<id>.yaml file in a dataset folder.",
        **Dish.model_json_schema(),
    }


def sources_schema() -> dict[str, Any]:
    return {
        "$schema": DIALECT,
        "title": "Sources",
        "description": "The sources.yaml file in a dataset folder: a list of sources.",
        **TypeAdapter(list[Source]).json_schema(),
    }


def main() -> None:
    SCHEMAS_DIR.mkdir(exist_ok=True)
    for name, schema in (
        ("ingredient", ingredient_schema()),
        ("dish", dish_schema()),
        ("sources", sources_schema()),
    ):
        text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
        (SCHEMAS_DIR / f"{name}.json").write_text(text, encoding="utf-8")
        print(f"wrote schemas/{name}.json")


if __name__ == "__main__":
    main()
