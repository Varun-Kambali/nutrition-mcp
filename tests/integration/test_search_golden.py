"""Golden search queries over the bundled data: each query and the food it must return first.

The table is written out, not generated from the data, so a renamed or dropped alias fails
here, and a coverage test makes every dish name and alias in the data appear in it.
"""

import json
from pathlib import Path

import pytest

from nutrition_mcp.config import Settings
from nutrition_mcp.loader import build_registry
from nutrition_mcp.registry import Registry
from nutrition_mcp.search import search_foods

SETTINGS = Settings(default_katori_g=150.0)
QUESTIONS = Path(__file__).parents[2] / "evals" / "datasets" / "questions.jsonl"

# Every dish's name and each of its aliases, spelled as the bundled data spells them.
DISH_NAMES: list[tuple[str, str]] = [
    ("Aloo gobi", "aloo_gobi"),
    ("aloo gobhi", "aloo_gobi"),
    ("alu gobi", "aloo_gobi"),
    ("Aloo posto", "aloo_posto"),
    ("alu posto", "aloo_posto"),
    ("aloo posta", "aloo_posto"),
    ("posto aloo", "aloo_posto"),
    ("Batata vada", "batata_vada"),
    ("batata wada", "batata_vada"),
    ("aloo vada", "batata_vada"),
    ("aloo bonda", "batata_vada"),
    ("Begun bhaja", "begun_bhaja"),
    ("baingan bhaja", "begun_bhaja"),
    ("fried brinjal slices", "begun_bhaja"),
    ("Bengali khichuri", "bengali_khichuri"),
    ("khichuri", "bengali_khichuri"),
    ("khichdi", "bengali_khichuri"),
    ("bhoger khichuri", "bengali_khichuri"),
    ("Bisi bele bath", "bisi_bele_bath"),
    ("bisibele bath", "bisi_bele_bath"),
    ("bisibelebath", "bisi_bele_bath"),
    ("Chana masala", "chana_masala"),
    ("chole", "chana_masala"),
    ("chhole", "chana_masala"),
    ("chickpea curry", "chana_masala"),
    ("Chapati", "chapati"),
    ("roti", "chapati"),
    ("phulka", "chapati"),
    ("fulka", "chapati"),
    ("poli", "chapati"),
    ("rotli", "chapati"),
    ("ruti", "chapati"),
    ("Cholar dal", "cholar_dal"),
    ("chholar dal", "cholar_dal"),
    ("bengali chana dal", "cholar_dal"),
    ("Coconut chutney", "coconut_chutney"),
    ("nariyal chutney", "coconut_chutney"),
    ("thengai chutney", "coconut_chutney"),
    ("kayi chutney", "coconut_chutney"),
    ("Curd rice", "curd_rice"),
    ("mosaranna", "curd_rice"),
    ("thayir sadam", "curd_rice"),
    ("dahi chawal", "curd_rice"),
    ("Dal makhani", "dal_makhani"),
    ("maa ki dal", "dal_makhani"),
    ("dal makhni", "dal_makhani"),
    ("Dal tadka", "dal_tadka"),
    ("tadka dal", "dal_tadka"),
    ("dal fry", "dal_tadka"),
    ("arhar dal tadka", "dal_tadka"),
    ("Dalma", "dalma"),
    ("dalama", "dalma"),
    ("odia dalma", "dalma"),
    ("Dhokla (khaman)", "dhokla"),
    ("khaman", "dhokla"),
    ("khaman dhokla", "dhokla"),
    ("besan dhokla", "dhokla"),
    ("Dimer dalna (egg curry)", "dimer_dalna"),
    ("dimer dalna", "dimer_dalna"),
    ("egg curry", "dimer_dalna"),
    ("anda curry", "dimer_dalna"),
    ("dim er dalna", "dimer_dalna"),
    ("Ghugni", "ghugni"),
    ("ghughni", "ghugni"),
    ("ghoogni", "ghugni"),
    ("Goan fish curry", "goan_fish_curry"),
    ("surmai curry", "goan_fish_curry"),
    ("kingfish curry", "goan_fish_curry"),
    ("goan kingfish curry", "goan_fish_curry"),
    ("Gujarati kadhi", "gujarati_kadhi"),
    ("gujrati kadhi", "gujarati_kadhi"),
    ("Idli", "idli"),
    ("idly", "idli"),
    ("idlis", "idli"),
    ("Jeera rice", "jeera_rice"),
    ("zeera rice", "jeera_rice"),
    ("cumin rice", "jeera_rice"),
    ("Lemon rice", "lemon_rice"),
    ("chitranna", "lemon_rice"),
    ("elumichai sadam", "lemon_rice"),
    ("nimbu chawal", "lemon_rice"),
    ("Machher jhol (rohu curry)", "machher_jhol"),
    ("macher jhol", "machher_jhol"),
    ("maacher jhol", "machher_jhol"),
    ("rui macher jhol", "machher_jhol"),
    ("fish jhol", "machher_jhol"),
    ("Methi thepla", "methi_thepla"),
    ("thepla", "methi_thepla"),
    ("methi na thepla", "methi_thepla"),
    ("Misal pav", "misal_pav"),
    ("misal", "misal_pav"),
    ("misal paav", "misal_pav"),
    ("Musur dal", "musur_dal"),
    ("musurir dal", "musur_dal"),
    ("bengali masoor dal", "musur_dal"),
    ("Palak paneer", "palak_paneer"),
    ("saag paneer", "palak_paneer"),
    ("spinach paneer", "palak_paneer"),
    ("Pav bhaji", "pav_bhaji"),
    ("pao bhaji", "pav_bhaji"),
    ("Plain dosa", "plain_dosa"),
    ("dosa", "plain_dosa"),
    ("sada dosa", "plain_dosa"),
    ("dosai", "plain_dosa"),
    ("Plain paratha", "plain_paratha"),
    ("paratha", "plain_paratha"),
    ("parantha", "plain_paratha"),
    ("tawa paratha", "plain_paratha"),
    ("Punjabi kadhi pakora", "punjabi_kadhi"),
    ("kadhi pakora", "punjabi_kadhi"),
    ("kadhi pakoda", "punjabi_kadhi"),
    ("punjabi kadhi", "punjabi_kadhi"),
    ("Rajma masala", "rajma_masala"),
    ("rajma curry", "rajma_masala"),
    ("Rasam", "rasam"),
    ("saaru", "rasam"),
    ("chaaru", "rasam"),
    ("tomato rasam", "rasam"),
    ("Rava upma", "rava_upma"),
    ("upma", "rava_upma"),
    ("uppittu", "rava_upma"),
    ("upit", "rava_upma"),
    ("sooji upma", "rava_upma"),
    ("Sabudana khichdi", "sabudana_khichdi"),
    ("sabudana khichadi", "sabudana_khichdi"),
    ("sago khichdi", "sabudana_khichdi"),
    ("Sambar", "sambar"),
    ("sambhar", "sambar"),
    ("huli", "sambar"),
    ("Shukto", "shukto"),
    ("shukta", "shukto"),
    ("sukto", "shukto"),
    ("Steamed rice", "steamed_rice"),
    ("plain rice", "steamed_rice"),
    ("cooked rice", "steamed_rice"),
    ("chawal", "steamed_rice"),
    ("bhaat", "steamed_rice"),
    ("bhat", "steamed_rice"),
    ("annam", "steamed_rice"),
    ("anna", "steamed_rice"),
    ("sadam", "steamed_rice"),
    ("Varan bhaat", "varan_bhaat"),
    ("varan bhat", "varan_bhaat"),
    ("varan bhath", "varan_bhaat"),
    ("dal bhaat", "varan_bhaat"),
    ("Ven pongal", "ven_pongal"),
    ("khara pongal", "ven_pongal"),
    ("pongal", "ven_pongal"),
    ("Zunka", "zunka"),
    ("jhunka", "zunka"),
]

# The foods in evals/datasets/questions.jsonl, named as the questions name them, in the
# singular: plurals such as "rotis" are a known gap (README, limitations).
QUESTION_NAMES: list[tuple[str, str]] = [
    ("roti", "chapati"),
    ("plain paratha", "plain_paratha"),
    ("palak paneer", "palak_paneer"),
    ("chole", "chana_masala"),
    ("jeera rice", "jeera_rice"),
    ("aloo gobi", "aloo_gobi"),
    ("dal tadka", "dal_tadka"),
    ("steamed rice", "steamed_rice"),
    ("idli", "idli"),
    ("idlis", "idli"),
    ("sambar", "sambar"),
    ("coconut chutney", "coconut_chutney"),
    ("plain dosa", "plain_dosa"),
    ("curd rice", "curd_rice"),
    ("lemon rice", "lemon_rice"),
    ("rasam", "rasam"),
    ("curd", "curd"),
    ("rava upma", "rava_upma"),
    ("machher jhol", "machher_jhol"),
    ("musur dal", "musur_dal"),
    ("begun bhaja", "begun_bhaja"),
    ("bengali khichuri", "bengali_khichuri"),
    ("dimer dalna", "dimer_dalna"),
    ("ghugni", "ghugni"),
    ("aloo posto", "aloo_posto"),
    ("pav bhaji", "pav_bhaji"),
    ("batata vada", "batata_vada"),
    ("khaman dhokla", "dhokla"),
    ("methi thepla", "methi_thepla"),
    ("dahi", "curd"),
    ("sabudana khichdi", "sabudana_khichdi"),
    ("milk", "whole_milk"),
    ("goan fish curry", "goan_fish_curry"),
]

# Names that differ from a word some other food spells exactly by an added or dropped letter.
SPELLING_VARIANTS: list[tuple[str, str]] = [
    ("chickpeas", "kabuli_chana"),
    ("kabuli chickpea", "kabuli_chana"),
    ("sorshe tel", "mustard_oil"),
    ("sorsher tel", "mustard_oil"),
    ("khichadi", "sabudana_khichdi"),
]

GOLDEN = DISH_NAMES + QUESTION_NAMES + SPELLING_VARIANTS

# Names a food is found by but not first. "rajma" is also the name of the Rajma ingredient,
# which ranks first; "rice" is in several names and raw rice ranks first; "chickpea" is a
# whole word of chana masala's alias "chickpea curry" but only the singular of "chickpeas".
FOUND_NOT_FIRST: list[tuple[str, str]] = [
    ("rajma", "rajma_masala"),
    ("rice", "steamed_rice"),
    ("chickpea", "kabuli_chana"),
]


@pytest.fixture(scope="module")
def reg() -> Registry:
    return build_registry([])


def found(reg: Registry, query: str) -> list[str]:
    return [food.id for food in search_foods(reg, SETTINGS, query, None, None, None, 50).results]


@pytest.mark.parametrize(("query", "food_id"), GOLDEN)
def test_a_golden_query_returns_its_food_first(reg: Registry, query: str, food_id: str) -> None:
    assert found(reg, query)[:1] == [food_id]


@pytest.mark.parametrize(("query", "food_id"), FOUND_NOT_FIRST)
def test_a_name_shared_with_other_foods_still_finds_the_food(
    reg: Registry, query: str, food_id: str
) -> None:
    assert food_id in found(reg, query)


def test_the_table_has_every_dish_name_and_alias(reg: Registry) -> None:
    in_data = {
        (name, f.id) for f in reg.foods() if f.kind == "dish" for name in [f.name, *f.aliases]
    }
    assert set(DISH_NAMES) == in_data
    assert len(DISH_NAMES) == len(in_data)


def test_the_table_has_every_food_in_the_questions() -> None:
    rows = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines()]
    asked = {item["food_id"] for row in rows for item in row["items"]}
    named = {food_id for _, food_id in QUESTION_NAMES + FOUND_NOT_FIRST}
    assert asked <= named


@pytest.mark.parametrize("query", ["chicken curry", "masala dosa", "pulao", "kheer"])
def test_a_dish_not_in_the_data_returns_nothing(reg: Registry, query: str) -> None:
    assert found(reg, query) == []


@pytest.mark.parametrize(
    ("query", "first", "never"),
    [
        # Sugar's alias "cheeni" folds to "chini", one letter changed from "chili".
        ("chilli", "green_chilli", "sugar"),
        ("chili", "green_chilli", "sugar"),
        ("dalma", "dalma", "dimer_dalna"),
        ("dalna", "dimer_dalna", "dalma"),
    ],
)
def test_a_changed_letter_does_not_return_another_food(
    reg: Registry, query: str, first: str, never: str
) -> None:
    results = found(reg, query)
    assert results[0] == first
    assert never not in results
