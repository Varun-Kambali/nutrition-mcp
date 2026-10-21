import pytest

from nutrition_mcp.config import Settings
from nutrition_mcp.loader import build_registry
from nutrition_mcp.models import Diet, Food, Nutrients, Region
from nutrition_mcp.registry import Registry
from nutrition_mcp.schemas import FoodSummary, SearchResult
from nutrition_mcp.search import (
    FILTERED_NOT_FOUND_NOTE,
    NOT_FOUND_NOTE,
    _has_every_word,
    normalise,
    search_foods,
)
from nutrition_mcp.units import valid_units

SETTINGS = Settings(default_katori_g=150.0)
DAL_DISHES = {"dal_tadka", "dal_makhani", "musur_dal", "cholar_dal", "varan_bhaat"}


@pytest.fixture(scope="module")
def reg() -> Registry:
    # The bundled dataset: the spellings under test are the ones real users will type.
    return build_registry([])


def make_food(
    food_id: str,
    name: str,
    regions: list[Region] | None = None,
    diet: Diet = "vegan",
    aliases: list[str] | None = None,
) -> Food:
    return Food(
        id=food_id,
        kind="dish",
        name=name,
        aliases=aliases or [],
        regions=regions or ["south"],
        diet=diet,
        source_ids=[],
        per_100g=Nutrients(),
        incomplete={},
        gi=None,
        gi_ref=None,
        measures={},
        serving_g=None,
        assumptions=None,
        density_g_per_ml=None,
    )


def ids(result: SearchResult) -> list[str]:
    return [food.id for food in result.results]


# normalise


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Sambhar", "sambar"),
        ("SAMBAAR", "sambar"),
        ("Chapathi", "chapati"),
        ("rotti", "roti"),
        ("daal", "dal"),
        ("batata wada", "batata vada"),
        ("Dhokla (khaman)", "dokla kaman"),
        ("dal_tadka", "dal tadka"),
        ("  Dal   Makhani ", "dal makani"),
        ("Pāyasam", "payasam"),
        ("chhole", "chole"),
        ("", ""),
        ("!?", ""),
    ],
)
def test_normalise(text: str, expected: str) -> None:
    assert normalise(text) == expected


def test_normalise_folds_long_vowels_before_collapsing_doubled_letters() -> None:
    # Collapsing first would turn "paneer" into "paner", which no spelling of it matches.
    assert normalise("paneer") == normalise("panir") == "panir"
    assert normalise("doodh") == normalise("dudh")


# search_foods


def test_exact_name(reg: Registry) -> None:
    result = search_foods(reg, SETTINGS, "Sambar", None, None, None)
    assert result.query == "Sambar"
    assert ids(result)[0] == "sambar"
    assert result.results[0].kind == "dish"
    assert result.results[0].regions == ["south"]
    assert result.suggestions == []


def test_results_are_the_same_summaries_the_queries_return(reg: Registry) -> None:
    result = search_foods(reg, SETTINGS, "sambar", None, None, None)
    sambar = reg.get("sambar")
    assert result.results[0] == FoodSummary.from_food(sambar, SETTINGS.default_katori_g)
    assert list(result.results[0].units) == valid_units(sambar, SETTINGS.default_katori_g)


def test_results_list_the_volume_units_of_a_liquid(reg: Registry) -> None:
    # Milk defines no measures of its own; its density is what makes ml, cup and glass work.
    milk = search_foods(reg, SETTINGS, "whole milk", None, None, None).results[0]
    assert milk.id == "whole_milk"
    density = reg.get("whole_milk").density_g_per_ml
    assert density is not None
    assert milk.units["glass"] == round(250 * density, 2)
    assert list(milk.units) == ["g", "ml", "tbsp", "tsp", "cup", "glass"]


def test_results_list_a_counted_dish_by_the_piece_with_no_katori(reg: Registry) -> None:
    chapati = search_foods(reg, SETTINGS, "chapati", None, None, None).results[0]
    assert chapati.id == "chapati"
    assert {"roti", "piece"} <= chapati.units.keys()
    assert "katori" not in chapati.units


def test_results_list_the_default_katori_from_the_settings() -> None:
    # A dish with no counted measure takes the default katori, and the setting decides its size.
    pickles = Registry({"a": make_food("a", "Mango pickle")}, {})
    found = search_foods(pickles, SETTINGS, "pickle", None, None, None).results[0]
    assert found.units == {"g": 1.0, "katori": 150.0}
    bigger = Settings(default_katori_g=200.0)
    assert search_foods(pickles, bigger, "pickle", None, None, None).results[0].units == {
        "g": 1.0,
        "katori": 200.0,
    }


def test_alias_matches_like_the_name(reg: Registry) -> None:
    assert ids(search_foods(reg, SETTINGS, "huli", None, None, None))[0] == "sambar"


@pytest.mark.parametrize(
    ("query", "dish_id"),
    [
        ("sambhar", "sambar"),
        ("chapathi", "chapati"),
        ("daal", "dal_tadka"),
        ("rotti", "chapati"),
    ],
)
def test_transliteration_variants(reg: Registry, query: str, dish_id: str) -> None:
    assert dish_id in ids(search_foods(reg, SETTINGS, query, None, None, None, limit=50))


@pytest.mark.parametrize(
    ("query", "dish_id"),
    [("sambhar", "sambar"), ("chapati", "chapati"), ("chapathi", "chapati"), ("rotti", "chapati")],
)
def test_variant_of_a_unique_name_ranks_that_dish_first(
    reg: Registry, query: str, dish_id: str
) -> None:
    assert ids(search_foods(reg, SETTINGS, query, None, None, None))[0] == dish_id


def test_ambiguous_returns_several(reg: Registry) -> None:
    result = search_foods(reg, SETTINGS, "dal", None, None, None, limit=50)
    dishes = [food for food in result.results if food.kind == "dish"]
    assert {food.id for food in dishes} >= DAL_DISHES
    # The caller sees what each match is and where it comes from, not one silent guess.
    assert all(food.regions for food in dishes)
    assert {food.kind for food in result.results} == {"dish", "ingredient"}


def test_typo_still_finds(reg: Registry) -> None:
    assert ids(search_foods(reg, SETTINGS, "sambaar", None, None, None))[0] == "sambar"
    # One letter short is still a match.
    assert ids(search_foods(reg, SETTINGS, "sambr", None, None, None))[0] == "sambar"


@pytest.mark.parametrize(
    ("query", "exact", "longer"),
    [
        ("paneer", "paneer", "palak_paneer"),
        ("curd", "curd", "curd_rice"),
        ("chapati", "chapati", "atta"),
    ],
)
def test_exact_name_ranks_before_a_longer_name_that_contains_it(
    reg: Registry, query: str, exact: str, longer: str
) -> None:
    # Palak paneer, curd rice and atta (alias "chapati flour") contain the whole query as a
    # word, so they tie with the exact name on word matching and sort after it.
    found = ids(search_foods(reg, SETTINGS, query, None, None, None, limit=50))
    assert found[0] == exact
    assert longer in found


def test_equal_scores_are_ordered_by_name() -> None:
    # Both names are 12 letters with no folds, so score and closeness tie and the name
    # decides. The ids run the other way, so the id would give the opposite order.
    pickles = Registry(
        {"a": make_food("a", "Mango pickle"), "b": make_food("b", "Lemon pickle")}, {}
    )
    result = search_foods(pickles, SETTINGS, "pickle", None, None, None)
    assert [food.name for food in result.results] == ["Lemon pickle", "Mango pickle"]


def test_equal_names_are_ordered_by_id() -> None:
    twins = Registry({"b": make_food("b", "Mango pickle"), "a": make_food("a", "Mango pickle")}, {})
    assert ids(search_foods(twins, SETTINGS, "pickle", None, None, None)) == ["a", "b"]


def test_region_filter(reg: Registry) -> None:
    result = search_foods(reg, SETTINGS, "dal", "east", None, None, limit=50)
    assert {"cholar_dal", "musur_dal"} <= set(ids(result))
    assert all("east" in food.regions for food in result.results)
    assert "dal_tadka" not in ids(result)


def test_diet_filter_means_suitable_for(reg: Registry) -> None:
    unfiltered = ids(search_foods(reg, SETTINGS, "curry", None, None, None, limit=50))
    assert {"goan_fish_curry", "dimer_dalna"} <= set(unfiltered)

    vegetarian = search_foods(reg, SETTINGS, "curry", None, "veg", None, limit=50)
    assert {food.diet for food in vegetarian.results} <= {"vegan", "veg"}
    assert not {"goan_fish_curry", "dimer_dalna"} & set(ids(vegetarian))

    # Vegan keeps only vegan food: dal tadka is vegetarian, musur dal is vegan.
    vegan = ids(search_foods(reg, SETTINGS, "dal", None, "vegan", None, limit=50))
    assert "musur_dal" in vegan
    assert "dal_tadka" not in vegan
    assert "dal_tadka" in ids(search_foods(reg, SETTINGS, "dal", None, "veg", None, limit=50))


def test_kind_filter(reg: Registry) -> None:
    dishes = search_foods(reg, SETTINGS, "rice", None, None, "dish", limit=50)
    assert dishes.results
    assert {food.kind for food in dishes.results} == {"dish"}
    ingredients = search_foods(reg, SETTINGS, "rice", None, None, "ingredient", limit=50)
    assert "white_rice" in ids(ingredients)
    assert {food.kind for food in ingredients.results} == {"ingredient"}


def test_filters_apply_before_suggestions_are_chosen(reg: Registry) -> None:
    # Dahi vada is not in the data but shares a word with batata vada, a west dish.
    plain = search_foods(reg, SETTINGS, "dahi vada", None, None, None)
    assert plain.results == []
    assert "Batata vada" in plain.suggestions

    north = search_foods(reg, SETTINGS, "dahi vada", "north", None, None)
    north_names = {food.name for food in reg.foods() if "north" in food.regions}
    assert north.results == []
    assert north.suggestions
    assert "Batata vada" not in north.suggestions
    assert set(north.suggestions) <= north_names


def test_no_match_is_empty_not_error(reg: Registry) -> None:
    # Nothing is close to the query, so there are no suggestions either.
    result = search_foods(reg, SETTINGS, "qwerty", None, None, None)
    assert result == SearchResult(
        query="qwerty", results=[], total=0, suggestions=[], note=NOT_FOUND_NOTE
    )


def test_filters_that_leave_no_foods_give_an_empty_result() -> None:
    pool = Registry(
        {
            "a": make_food("a", "Fish curry", ["east"], "non_veg"),
            "b": make_food("b", "Sambar", ["south"]),
        },
        {},
    )
    # The food is there without the diet filter, and no vegan dish is from the east.
    assert ids(search_foods(pool, SETTINGS, "fish curry", "east", None, None)) == ["a"]
    result = search_foods(pool, SETTINGS, "fish curry", "east", "vegan", None)
    assert result == SearchResult(
        query="fish curry", results=[], total=0, suggestions=[], note=FILTERED_NOT_FOUND_NOTE
    )


def test_suggestions_come_only_when_nothing_matched(reg: Registry) -> None:
    assert search_foods(reg, SETTINGS, "sambar", None, None, None).suggestions == []
    assert search_foods(reg, SETTINGS, "dahi vada", None, None, None).suggestions != []


# An empty result says what it means. The first grounding run searched for pulao 4 times,
# got no results each time, then searched "rice" and answered with jeera rice.


def test_an_empty_result_says_the_food_is_not_in_the_data(reg: Registry) -> None:
    result = search_foods(reg, SETTINGS, "pulao", None, None, None)
    assert result.results == []
    assert result.suggestions != []
    assert result.note == NOT_FOUND_NOTE


def test_the_note_says_to_report_the_food_missing_instead_of_substituting() -> None:
    assert NOT_FOUND_NOTE.startswith("Not found:")
    assert "report that food as missing" in NOT_FOUND_NOTE
    # The three ways the first run found something else to answer with.
    for substitute in ("a suggestion", "a broader match", "other foods put together"):
        assert substitute in NOT_FOUND_NOTE


@pytest.mark.parametrize(
    ("query", "region", "diet", "kind"),
    [
        ("sambar", "north", None, None),
        ("dimer dalna", None, "veg", None),
        ("sambar", None, None, "ingredient"),
    ],
)
def test_an_empty_result_under_a_filter_does_not_say_the_food_is_missing(
    reg: Registry, query: str, region: str | None, diet: str | None, kind: str | None
) -> None:
    # The food is in the data, outside the filter. Saying "not in the data" here would have
    # the model report it missing.
    assert search_foods(reg, SETTINGS, query, None, None, None).results != []
    result = search_foods(reg, SETTINGS, query, region, diet, kind)
    assert result.results == []
    assert result.note == FILTERED_NOT_FOUND_NOTE
    assert "Search again without those filters" in FILTERED_NOT_FOUND_NOTE


def test_a_search_with_results_has_no_note(reg: Registry) -> None:
    assert search_foods(reg, SETTINGS, "sambar", None, None, None).note is None


def test_suggestions_are_capped_at_five() -> None:
    # Six names are close to the query, and none holds both of its words.
    names = ["Dahi bonda", "Dal vada", "Medu vada", "Methi vada", "Sabudana vada", "Palak vada"]
    pool = Registry({str(i): make_food(str(i), name) for i, name in enumerate(names)}, {})
    result = search_foods(pool, SETTINGS, "dahi vada", None, None, None)
    assert result.results == []
    assert len(result.suggestions) == 5
    assert set(result.suggestions) < set(names)


# A short query must match whole words, not letters inside other words.


def test_tea_is_not_found_inside_steamed(reg: Registry) -> None:
    assert "steamed_rice" not in ids(search_foods(reg, SETTINGS, "tea", None, None, None, limit=50))


def test_egg_finds_the_egg_not_brinjal(reg: Registry) -> None:
    found = ids(search_foods(reg, SETTINGS, "egg", None, None, None, limit=50))
    assert "egg" in found
    assert not {"brinjal", "begun_bhaja"} & set(found)


def test_chana_is_not_found_inside_an_alias_of_rice(reg: Registry) -> None:
    found = ids(search_foods(reg, SETTINGS, "chana", None, None, None, limit=50))
    assert {"chana_dal", "kabuli_chana", "chana_masala"} <= set(found)
    assert "steamed_rice" not in found


def test_dal_finds_dals_not_words_that_contain_dal(reg: Registry) -> None:
    found = set(ids(search_foods(reg, SETTINGS, "dal", None, None, None, limit=50)))
    assert {"toor_dal", "musur_dal", "varan_bhaat"} <= found
    # The Tamil aliases "kadalai maavu" and "verkadalai" only contain the letters.
    assert not {"besan", "peanuts"} & found


def test_a_short_alias_inside_a_longer_query_is_not_a_match(reg: Registry) -> None:
    # "aloo" is an alias of potato and "paratha" of plain paratha, but the query holds words
    # that neither has, so neither is a match; plain paratha is only a close name.
    result = search_foods(reg, SETTINGS, "aloo paratha", None, None, None)
    assert result.results == []
    assert "Plain paratha" in result.suggestions


def test_extra_words_do_not_match_through_one_ingredient(reg: Registry) -> None:
    assert search_foods(reg, SETTINGS, "paneer butter masala", None, None, None).results == []


def test_a_variant_spelling_of_a_two_word_dish_finds_only_that_dish(reg: Registry) -> None:
    # "pao" is also an alias of the pav ingredient, which is not what was asked for.
    found = ids(search_foods(reg, SETTINGS, "pao bhaji", None, None, None))
    assert "pav_bhaji" in found
    assert "pav" not in found


def test_all_the_query_words_in_a_longer_name_is_a_match(reg: Registry) -> None:
    found = ids(search_foods(reg, SETTINGS, "dal", None, None, None, limit=50))
    assert {"dal_tadka", "musur_dal", "varan_bhaat"} <= set(found)


def test_a_typo_inside_a_two_word_query_is_found(reg: Registry) -> None:
    assert ids(search_foods(reg, SETTINGS, "chana masla", None, None, None))[0] == "chana_masala"


# A word that is spelled exactly somewhere in the pool rules out one-edit matches for it.


@pytest.mark.parametrize("query", ["chilli", "chili"])
def test_an_exact_word_beats_a_one_edit_word(reg: Registry, query: str) -> None:
    # Sugar's alias "cheeni" folds to "chini", one edit from "chili", and scores 80 as a whole.
    found = ids(search_foods(reg, SETTINGS, query, None, None, None, limit=50))
    assert {"green_chilli", "red_chilli_powder"} <= set(found)
    assert "sugar" not in found


@pytest.mark.parametrize(
    ("query", "food_id"),
    [
        # "chickpea" is spelled exactly in chana masala's alias "chickpea curry".
        ("chickpea", "kabuli_chana"),
        ("kabuli chickpea", "kabuli_chana"),
        # "shorshe", an alias of mustard seeds, folds to "sorse"; the oil's alias is "sorsher tel".
        ("sorshe tel", "mustard_oil"),
        # "khichadi", an alias of sabudana khichdi, is one letter longer than "khichdi".
        ("khichadi", "bengali_khichuri"),
    ],
)
def test_a_letter_added_or_dropped_still_matches_beside_an_exact_word(
    reg: Registry, query: str, food_id: str
) -> None:
    assert food_id in ids(search_foods(reg, SETTINGS, query, None, None, None, limit=50))


def test_mirch_and_mirchi_match_each_other() -> None:
    pool = Registry({"a": make_food("a", "Hari mirch"), "b": make_food("b", "Mirchi powder")}, {})
    assert ids(search_foods(pool, SETTINGS, "hari mirchi", None, None, None)) == ["a"]
    assert ids(search_foods(pool, SETTINGS, "mirch powder", None, None, None)) == ["b"]


@pytest.mark.parametrize(("query", "food_id"), [("dalma", "dalma"), ("dalna", "dimer_dalna")])
def test_a_changed_letter_gives_way_to_the_exact_word(
    reg: Registry, query: str, food_id: str
) -> None:
    # "dalma" and "dalna" are one letter apart and the same length: two different dishes.
    assert ids(search_foods(reg, SETTINGS, query, None, None, None, limit=50)) == [food_id]


def test_an_exact_word_rules_out_only_a_changed_letter() -> None:
    pool = Registry(
        {
            "a": make_food("a", "Samber rice"),
            "b": make_food("b", "Sambar"),
            "c": make_food("c", "Sambers"),
        },
        {},
    )
    # "sambar" changes a letter of "samber"; "sambers" adds one.
    assert ids(search_foods(pool, SETTINGS, "samber", None, None, None)) == ["a", "c"]


def test_an_exact_word_only_in_an_alias_rules_out_a_changed_letter() -> None:
    pool = Registry(
        {"a": make_food("a", "Lentil stew", aliases=["Samber"]), "b": make_food("b", "Sambar")},
        {},
    )
    assert ids(search_foods(pool, SETTINGS, "samber", None, None, None)) == ["a"]


def test_an_exact_word_only_in_an_id_rules_out_a_changed_letter() -> None:
    pool = Registry(
        {"samber_rice": make_food("samber_rice", "Rice bowl"), "b": make_food("b", "Sambar")}, {}
    )
    assert ids(search_foods(pool, SETTINGS, "samber", None, None, None)) == ["samber_rice"]


def test_a_repeated_query_word_finds_the_same_foods(reg: Registry) -> None:
    once = search_foods(reg, SETTINGS, "dal", None, None, None, limit=50)
    twice = search_foods(reg, SETTINGS, "dal dal", None, None, None, limit=50)
    assert set(ids(twice)) == set(ids(once))
    assert twice.total == once.total
    pool = Registry({"a": make_food("a", "Samber rice"), "b": make_food("b", "Sambar")}, {})
    assert ids(search_foods(pool, SETTINGS, "samber samber", None, None, None)) == ["a"]


@pytest.mark.parametrize(("query", "name"), [("sag", "Sago"), ("dosa", "Dosai"), ("dosai", "Dosa")])
def test_a_word_under_five_letters_matches_only_an_equal_word(query: str, name: str) -> None:
    # Each pair is one letter apart and scores 80 or more as a whole string.
    pool = Registry({"a": make_food("a", name)}, {})
    assert search_foods(pool, SETTINGS, query, None, None, None).results == []
    assert ids(search_foods(pool, SETTINGS, name, None, None, None)) == ["a"]


@pytest.mark.parametrize("words", [["dal"], ["tadka", "dal"]])
def test_an_equal_word_matches_whatever_the_pool_spells(words: list[str]) -> None:
    # Equal words are read off the name alone, so the check does not depend on
    # spelled_exactly coming from the same foods as the name.
    assert _has_every_word(words, ["dal", "tadka"], set())
    assert _has_every_word(words, ["dal", "tadka"], set(words))


def test_a_typo_is_found_when_no_food_has_the_typed_word() -> None:
    pool = Registry({"a": make_food("a", "Sambar")}, {})
    assert ids(search_foods(pool, SETTINGS, "samber", None, None, None)) == ["a"]


def test_a_food_with_the_exact_word_is_the_only_result_for_it() -> None:
    pool = Registry(
        {"a": make_food("a", "Samber rice", ["north"]), "b": make_food("b", "Sambar")}, {}
    )
    assert ids(search_foods(pool, SETTINGS, "samber", None, None, None)) == ["a"]


def test_the_exact_word_rule_is_per_query_word() -> None:
    # "samber" is spelled exactly in one food, but "masla" is not spelled exactly anywhere.
    pool = Registry(
        {
            "a": make_food("a", "Samber rice"),
            "b": make_food("b", "Sambar masala"),
            "c": make_food("c", "Samber masala"),
        },
        {},
    )
    assert ids(search_foods(pool, SETTINGS, "samber masla", None, None, None)) == ["c"]


def test_a_food_a_filter_removes_does_not_rule_out_a_typo() -> None:
    pool = Registry(
        {"a": make_food("a", "Samber rice", ["north"]), "b": make_food("b", "Sambar")}, {}
    )
    assert ids(search_foods(pool, SETTINGS, "samber", "south", None, None)) == ["b"]


# A food is a result only when one of its names holds every query word.


@pytest.mark.parametrize(
    ("query", "other_food"),
    [
        # Each wrong food has a name that scores 80 or more against the query as a whole
        # string, through an alias one word or a few letters off.
        ("chicken curry", "chana_masala"),  # alias "chickpea curry"
        ("masala dosa", "plain_dosa"),  # alias "sada dosa"
        ("pulao", "chapati"),  # alias "phulka"
        ("kheer", "cucumber"),  # alias "khira"
        ("saag", "sabudana"),  # alias "sago"
        ("vada", "ginger"),  # alias "ada"
        ("toor dal", "urad_dal"),
        # "chole" and "whole" are one letter apart, but the first letters differ.
        ("chole", "whole_milk"),
    ],
)
def test_a_name_a_word_or_letter_off_is_not_a_result(
    reg: Registry, query: str, other_food: str
) -> None:
    assert other_food not in ids(search_foods(reg, SETTINGS, query, None, None, None, limit=50))


def test_a_dish_not_in_the_data_offers_close_names_only_as_suggestions(reg: Registry) -> None:
    result = search_foods(reg, SETTINGS, "chicken curry", None, None, None)
    assert result.results == []
    assert result.total == 0
    assert "Chana masala" in result.suggestions


def test_every_query_word_must_match_the_same_name(reg: Registry) -> None:
    # "chana" alone is in chana dal; "masla" is one letter off "masala" only in chana masala.
    chana = ids(search_foods(reg, SETTINGS, "chana masla", None, None, None, limit=50))
    assert "chana_masala" in chana
    assert "chana_dal" not in chana
    toor = ids(search_foods(reg, SETTINGS, "toor dal", None, None, None, limit=50))
    assert toor[0] == "toor_dal"
    assert not {"urad_dal", "musur_dal", "chana_dal"} & set(toor)


def test_khichdi_finds_both_khichdis(reg: Registry) -> None:
    # Bengali khichuri is the same family of dish, so it carries "khichdi" as an alias.
    found = ids(search_foods(reg, SETTINGS, "khichdi", None, None, None, limit=50))
    assert {"bengali_khichuri", "sabudana_khichdi"} <= set(found)


def test_a_one_letter_slip_still_needs_a_close_name(reg: Registry) -> None:
    # "butter" folds to "buter", one letter off "biter" in "bitter gourd", but the names as a
    # whole are far apart, so bitter gourd is not a result.
    found = ids(search_foods(reg, SETTINGS, "butter", None, None, None, limit=50))
    assert "butter" in found
    assert "bitter_gourd" not in found


def test_a_spelling_variant_of_a_dal_is_found(reg: Registry) -> None:
    assert "musur_dal" in ids(search_foods(reg, SETTINGS, "masoor dal", None, None, None))


def test_total_counts_matches_before_the_limit(reg: Registry) -> None:
    cut = search_foods(reg, SETTINGS, "dal", None, None, None, limit=3)
    assert len(cut.results) == 3
    assert cut.total > 3
    whole = search_foods(reg, SETTINGS, "dal", None, None, None, limit=50)
    assert whole.total == len(whole.results)
    assert cut.total == whole.total
    assert search_foods(reg, SETTINGS, "qwerty", None, None, None).total == 0


def test_one_letter_slip_in_a_long_name_is_found(reg: Registry) -> None:
    assert ids(search_foods(reg, SETTINGS, "samber", None, None, None))[0] == "sambar"


def test_limit_caps_results(reg: Registry) -> None:
    assert len(search_foods(reg, SETTINGS, "dal", None, None, None, limit=3).results) == 3
    assert len(search_foods(reg, SETTINGS, "dal", None, None, None).results) == 10


@pytest.mark.parametrize("limit", [0, -1, 51])
def test_limit_out_of_range_is_rejected(reg: Registry, limit: int) -> None:
    with pytest.raises(ValueError, match="limit must be 1 to 50"):
        search_foods(reg, SETTINGS, "dal", None, None, None, limit=limit)


def test_query_longer_than_100_characters_is_rejected(reg: Registry) -> None:
    with pytest.raises(ValueError, match="at most 100 characters"):
        search_foods(reg, SETTINGS, "x" * 101, None, None, None)
    # 100 characters is allowed.
    assert search_foods(reg, SETTINGS, "dal " * 25, None, None, None).results


@pytest.mark.parametrize("query", ["", "   ", "!?", "()"])
def test_blank_query_is_rejected(reg: Registry, query: str) -> None:
    with pytest.raises(ValueError, match="query"):
        search_foods(reg, SETTINGS, query, None, None, None)


@pytest.mark.parametrize(
    ("region", "diet", "kind", "label"),
    [("central", None, None, "region"), (None, "keto", None, "diet"), (None, None, "meal", "kind")],
)
def test_unknown_filter_value_is_rejected(
    reg: Registry, region: str | None, diet: str | None, kind: str | None, label: str
) -> None:
    with pytest.raises(ValueError, match=f"unknown {label}"):
        search_foods(reg, SETTINGS, "dal", region, diet, kind)
