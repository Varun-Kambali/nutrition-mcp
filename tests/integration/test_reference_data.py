import csv
from pathlib import Path

from nutrition_mcp.loader import bundled_dir, load_dataset

ROOT = Path(__file__).parents[2]
MAP_PATH = ROOT / "data_build" / "reference_map.csv"
REFERENCES_PATH = ROOT / "evals" / "datasets" / "references.csv"
SOURCE_IDS = {"usda": "usda_fdc", "cofid": "cofid_2021"}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_references_csv_matches_the_map() -> None:
    # references.csv is written by scripts/build_references.py from the map, which needs the
    # source records CI does not have. This catches a map edit without a rebuild.
    expected = [
        (row["dish_id"], SOURCE_IDS[row["source"]], row["record"]) for row in _rows(MAP_PATH)
    ]
    actual = [(row["dish_id"], row["source_id"], row["record"]) for row in _rows(REFERENCES_PATH)]
    assert actual == expected


def test_every_reference_names_a_bundled_dish() -> None:
    _, _, dishes = load_dataset(bundled_dir())
    assert {row["dish_id"] for row in _rows(MAP_PATH)} <= {dish.id for dish in dishes}
