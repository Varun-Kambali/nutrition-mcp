"""Extract the Indian rows of the GI compendium into data_build/gi_compendium_india.csv.

Usage: `make gi`. No API key is needed.

The source is Henry CJ et al., "A glycaemic index compendium of non-western foods",
Nutrition & Diabetes 11, 2 (2021), CC BY 4.0. The script downloads its full text from
Europe PMC once into .cache/, checks the file's SHA-256, reads Table 1 (940 rows, the
paper's only table) and keeps a row when:

1. the study was done in India, or the food name is an Indian dish (INDIAN_FOODS), and
2. its GI is on the glucose scale, and
3. its GI cell holds one number.

Rows that pass step 1 but fail step 2 or 3 are printed with the reason, so the drops can be
reviewed. Rows a bundled dish or ingredient uses carry the matching notes from MATCH_NOTES;
edit those notes whenever a gi_ref changes, then run `make gi` again.
"""

import csv
import hashlib
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data_build" / "gi_compendium_india.csv"
CACHE_PATH = ROOT / ".cache" / "gi_compendium_PMC7791047.xml"

XML_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC7791047/fullTextXML"
# The file as downloaded on 2026-10-20. A different hash means Europe PMC changed the file,
# and every row needs a fresh review before the hash here is updated. The check runs before
# the XML is parsed, so only this known file ever reaches the parser.
XML_SHA256 = "4e884fc1db405be6a3f8c40b08bd474dd6d74d4149b2e8aebfba488d047c05f9"

TABLE_ID = "Tab1"
TABLE_NUMBER = 1
HEADER = [
    "",
    "Reference",
    "Country",
    "Food item",
    "GI (glucose = 100)",
    "Serving size (g) per experimental portion size",
    "Available CHO (g) per experimental portion",
    "Participants (type and number)",
    "Reference food and time period",
]
CSV_COLUMNS = ["row_id", "table", "food", "country", "gi", "reference", "notes"]

# Exact match: "West India" in the table is the West Indies (ref 114 is a study of
# Caribbean foods), not western India.
INDIA = "India"

# Dish names that mark a row tested outside India as an Indian food. Sri Lankan spellings of
# shared South Indian dishes are included: thosai (dosa), pittu (puttu), hopper (appam),
# string hopper (idiyappam), sampar (sambar). Whether a kept row matches one of our dishes
# is a separate, manual decision recorded in MATCH_NOTES.
INDIAN_FOODS = re.compile(
    r"\b(?:idli|dosa|thosai|upma|chapp?att?h?i|rott?i|paratha|parantha|porotta|poori"
    r"|puttu|pittu|idiyappam|appam|hoppers?|basmati|khichdi|khichuri|dh?al|dahl"
    r"|sambar|sampar|biryani|samosa|sambosa|poha|thepla|vada)\b",
    re.IGNORECASE,
)

# The column header says "glucose = 100", but some rows name white bread or white rice as the
# reference food. Ref 153 reports the same Sri Lankan breads against both glucose and white
# bread (white bread 100 on the second scale), so those rows are on the reference food's own
# scale. The paper gives no conversion factor, so they are dropped rather than converted.
# Dextrose is glucose under another name.
GLUCOSE_REFERENCES = frozenset({"glucose", "dextrose"})

# Notes for the rows that bundled foods use: which food, the candidate rows considered and
# why this row was chosen. The matching rule is in docs/data.md, "Glycemic index".
MATCH_NOTES = {
    "T1-664": (
        "Used for bengali_khichuri. Only candidate: T1-664; T1-663 is barnyard millet khichdi, "
        "a different grain. Khichuri is the Bengali name for khichdi: rice and dal cooked soft "
        "together. The compendium does not name the dal, and our recipe adds potato, "
        "cauliflower and peas."
    ),
    "T1-666": (
        "Used for steamed_rice. Only candidate: T1-666, white rice cooked with water at 1:2 "
        "(the serving column). Not used: T1-619 to T1-621 are parboiled varieties, T1-683 to "
        "T1-685 are one parboiled variety at three degrees of polish (ref 141), T1-633 is "
        "basmati and T1-665 a high-fibre variety."
    ),
    "T1-688": (
        "Used for plain_dosa. Only Indian candidate on the glucose scale: T1-688. Not used: "
        "T1-667 (rice dosa, white bread scale, dropped), T1-719 (millet dosa), T1-047 (rice "
        "dosa tested in Singapore, used only when no Indian row exists)."
    ),
    "T1-691": (
        "Used for chapati. Candidates, each a plain wheat chapati or roti from an Indian "
        "study: T1-711 (44.6), T1-673 (48.37), T1-691 (54.43), T1-657 (64), T1-631 (83.92); "
        "this is the median. Not used: T1-615 to T1-618, T1-632, T1-651, T1-674 and T1-712 "
        "add amaranth, rice bran, sorghum, carrot or multigrain flour."
    ),
    "T1-697": (
        "Used for rava_upma. Candidates, each a semolina upma from an Indian study: T1-658 "
        "(58), T1-697 (62.37), T1-659 (67); this is the median. Not used: T1-628 and T1-630 "
        "are vermicelli (sewai) upma, T1-652 and T1-653 sorghum, T1-707 to T1-709 add finger "
        "millet, T1-048 was tested in Singapore."
    ),
    "T1-698": (
        "Used for idli. Candidates, each a plain idli from an Indian study: T1-698 (62.45) "
        "and T1-627 (67.11); with two, the lower middle value is used. Not used: T1-629 (kodo "
        "millet idli), T1-046 (tested in Singapore), T1-842 to T1-847 (idli eaten with "
        "sambol or sampar)."
    ),
    "T1-699": (
        "Used for methi_thepla. Only candidate: T1-699; T1-700 adds ashwagandha. The "
        "compendium does not say whether this thepla had methi leaves; thepla is a wheat "
        "flatbread cooked on a tawa with oil, like our recipe."
    ),
}


class ExtractError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Row:
    row_id: str
    food: str
    country: str
    gi_text: str
    reference: str
    reference_food: str


def _text(element: ET.Element) -> str:
    return " ".join("".join(element.itertext()).split())


def xml_path() -> Path:
    if not CACHE_PATH.exists():
        print(f"Downloading the compendium to {CACHE_PATH}", file=sys.stderr)
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        response = httpx.get(XML_URL, follow_redirects=True, timeout=120)
        response.raise_for_status()
        partial = CACHE_PATH.with_suffix(".part")
        partial.write_bytes(response.content)
        partial.rename(CACHE_PATH)
    digest = hashlib.sha256(CACHE_PATH.read_bytes()).hexdigest()
    if digest != XML_SHA256:
        raise ExtractError(f"{CACHE_PATH}: SHA-256 is {digest}, expected {XML_SHA256}")
    return CACHE_PATH


def _citations(root: ET.Element) -> dict[str, str]:
    """Reference list ID ("CR134") to "ref 134", plus the study's DOI when the paper has one.

    Author names are left out: the paper's citation metadata spells them inconsistently, and
    the reference number leads to the full citation in the paper.
    """
    citations: dict[str, str] = {}
    for ref in root.iter("ref"):
        text = f"ref {ref.findtext('label', '').rstrip('.')}"
        for link in ref.iter("ext-link"):
            if link.get("ext-link-type") == "doi":
                text += f", doi:{link.get('{http://www.w3.org/1999/xlink}href', '')}"
                break
        citations[ref.get("id", "")] = text
    return citations


def read_table(path: Path) -> Iterator[Row]:
    root = ET.parse(path).getroot()
    table = next(
        (wrap.find("table") for wrap in root.iter("table-wrap") if wrap.get("id") == TABLE_ID),
        None,
    )
    if table is None:
        raise ExtractError(f"{path}: table {TABLE_ID} not found")
    header = [_text(cell) for cell in table.iter("th")][: len(HEADER)]
    if header != HEADER:
        raise ExtractError(f"{path}: unexpected table header {header}")
    citations = _citations(root)
    body = table.find("tbody")
    if body is None:
        raise ExtractError(f"{path}: table {TABLE_ID} has no body")
    for tr in body.iter("tr"):
        cells = tr.findall("td")
        if len(cells) != 10:
            raise ExtractError(f"{path}: row {_text(cells[0])} has {len(cells)} cells")
        number, ref_cell, country, food, gi, *_rest, reference_food = cells
        # One row (561, Hong Kong) cites its study in plain text instead of a link.
        xref = ref_cell.find(".//xref")
        linked = citations.get(xref.get("rid", "")) if xref is not None else None
        reference = linked or _text(ref_cell)
        if not reference:
            raise ExtractError(f"{path}: row {_text(number)} cites no reference")
        yield Row(
            row_id=f"T{TABLE_NUMBER}-{int(_text(number)):03d}",
            food=_text(food),
            country=_text(country),
            gi_text=_text(gi),
            reference=reference,
            reference_food=_text(reference_food),
        )


def is_indian(row: Row) -> bool:
    return row.country == INDIA or INDIAN_FOODS.search(row.food) is not None


def drop_reason(row: Row) -> str | None:
    # "Glucose/2 h" and "Glucose (50 g CHO)/4 h" both name glucose; "Glucose and rice/4 h"
    # names two reference foods and is not on one scale.
    reference = re.sub(r"\(.*?\)", "", row.reference_food.split("/")[0]).strip().lower()
    if reference not in GLUCOSE_REFERENCES:
        return f"reference food is {row.reference_food}, not glucose, and no factor is given"
    try:
        float(row.gi_text)
    except ValueError:
        return f"GI cell '{row.gi_text}' is not a single number"
    return None


def _number(text: str) -> str:
    # "68.70" -> "68.7", "45.0" -> "45": the same value, written one way.
    return f"{float(text):.2f}".rstrip("0").rstrip(".")


def write_csv(rows: list[Row], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            notes = f"Reference food: {row.reference_food}."
            if row.row_id in MATCH_NOTES:
                notes += " " + MATCH_NOTES[row.row_id]
            writer.writerow(
                [
                    row.row_id,
                    TABLE_NUMBER,
                    row.food,
                    row.country,
                    _number(row.gi_text),
                    row.reference,
                    notes,
                ]
            )


def main() -> int:
    rows = list(read_table(xml_path()))
    ids = [row.row_id for row in rows]
    if len(set(ids)) != len(ids):
        raise ExtractError("row numbers in the table repeat")
    kept: list[Row] = []
    for row in filter(is_indian, rows):
        reason = drop_reason(row)
        if reason is None:
            kept.append(row)
        else:
            print(f"dropped {row.row_id} {row.country}: {row.food}: {reason}")
    unknown = set(MATCH_NOTES) - {row.row_id for row in kept}
    if unknown:
        raise ExtractError(f"MATCH_NOTES names rows that were not kept: {sorted(unknown)}")
    write_csv(kept, OUT_PATH)
    in_india = sum(row.country == INDIA for row in kept)
    print(
        f"\nWrote {len(kept)} of {len(rows)} rows to {OUT_PATH.relative_to(ROOT)}: "
        f"{in_india} tested in India, {len(kept) - in_india} naming an Indian food elsewhere"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ExtractError as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)
