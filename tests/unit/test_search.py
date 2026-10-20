import pytest

from nutrition_mcp.loader import build_registry
from nutrition_mcp.registry import Registry
from nutrition_mcp.schemas import FoodSummary, SearchResult
from nutrition_mcp.search import normalise, search_foods

DAL_DISHES = {"dal_tadka", "dal_makhani", "musur_dal", "cholar_dal", "dalma", "varan_bhaat"}


@pytest.fixture(scope="module")
def reg() -> Registry:
    # The bundled dataset: the spellings under test are the ones real users will type.
    return build_registry([])


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
    result = search_foods(reg, "Sambar", None, None, None)
    assert result.query == "Sambar"
    assert ids(result)[0] == "sambar"
    assert result.results[0].kind == "dish"
    assert result.results[0].regions == ["south"]
    assert result.suggestions == []


def test_results_are_the_same_summaries_the_queries_return(reg: Registry) -> None:
    result = search_foods(reg, "sambar", None, None, None)
    assert result.results[0] == FoodSummary.from_food(reg.get("sambar"))
    assert result.results[0].measures == reg.get("sambar").measures


def test_alias_matches_like_the_name(reg: Registry) -> None:
    assert ids(search_foods(reg, "huli", None, None, None))[0] == "sambar"


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
    assert dish_id in ids(search_foods(reg, query, None, None, None, limit=50))


@pytest.mark.parametrize(
    ("query", "dish_id"), [("sambhar", "sambar"), ("chapathi", "chapati"), ("rotti", "chapati")]
)
def test_variant_of_a_unique_name_ranks_that_dish_first(
    reg: Registry, query: str, dish_id: str
) -> None:
    assert ids(search_foods(reg, query, None, None, None))[0] == dish_id


def test_ambiguous_returns_several(reg: Registry) -> None:
    result = search_foods(reg, "dal", None, None, None, limit=50)
    dishes = [food for food in result.results if food.kind == "dish"]
    assert {food.id for food in dishes} >= DAL_DISHES
    # The caller sees what each match is and where it comes from, not one silent guess.
    assert all(food.regions for food in dishes)
    assert {food.kind for food in result.results} == {"dish", "ingredient"}


def test_typo_still_finds(reg: Registry) -> None:
    assert ids(search_foods(reg, "sambaar", None, None, None))[0] == "sambar"
    # One letter short is still a match.
    assert ids(search_foods(reg, "sambr", None, None, None))[0] == "sambar"


def test_better_match_ranks_first(reg: Registry) -> None:
    assert ids(search_foods(reg, "paneer", None, None, None))[:2] == ["paneer", "palak_paneer"]


def test_equal_scores_are_ordered_by_name(reg: Registry) -> None:
    # Every east-region dal dish contains "dal" and scores the same.
    result = search_foods(reg, "dal", "east", None, None, limit=50)
    names = [food.name for food in result.results]
    assert len(names) > 1
    assert names == sorted(names)


def test_region_filter(reg: Registry) -> None:
    result = search_foods(reg, "dal", "east", None, None, limit=50)
    assert {"cholar_dal", "dalma", "musur_dal"} <= set(ids(result))
    assert all("east" in food.regions for food in result.results)
    assert "dal_tadka" not in ids(result)


def test_diet_filter_means_suitable_for(reg: Registry) -> None:
    unfiltered = ids(search_foods(reg, "curry", None, None, None, limit=50))
    assert {"goan_fish_curry", "dimer_dalna"} <= set(unfiltered)

    vegetarian = search_foods(reg, "curry", None, "veg", None, limit=50)
    assert {food.diet for food in vegetarian.results} <= {"vegan", "veg"}
    assert not {"goan_fish_curry", "dimer_dalna"} & set(ids(vegetarian))

    # Vegan keeps only vegan food: dal tadka is vegetarian, musur dal is vegan.
    vegan = ids(search_foods(reg, "dal", None, "vegan", None, limit=50))
    assert "musur_dal" in vegan
    assert "dal_tadka" not in vegan
    assert "dal_tadka" in ids(search_foods(reg, "dal", None, "veg", None, limit=50))


def test_kind_filter(reg: Registry) -> None:
    dishes = search_foods(reg, "rice", None, None, "dish", limit=50)
    assert dishes.results
    assert {food.kind for food in dishes.results} == {"dish"}
    ingredients = search_foods(reg, "rice", None, None, "ingredient", limit=50)
    assert ids(ingredients) == ["white_rice"]


def test_filters_apply_before_suggestions_are_chosen(reg: Registry) -> None:
    # Sambal is not in the data but is close to sambar, which is a south dish.
    plain = search_foods(reg, "sambal", None, None, None)
    assert plain.results == []
    assert plain.suggestions[0] == "Sambar"

    north = search_foods(reg, "sambal", "north", None, None)
    north_names = {food.name for food in reg.foods() if "north" in food.regions}
    assert north.results == []
    assert "Sambar" not in north.suggestions
    assert set(north.suggestions) <= north_names


def test_no_match_is_empty_not_error(reg: Registry) -> None:
    result = search_foods(reg, "qwerty", None, None, None)
    assert result.results == []
    assert len(result.suggestions) <= 5


def test_suggestions_come_only_when_nothing_matched(reg: Registry) -> None:
    assert search_foods(reg, "sambar", None, None, None).suggestions == []
    assert search_foods(reg, "sambal", None, None, None).suggestions != []


def test_suggestions_are_capped_at_five(reg: Registry) -> None:
    result = search_foods(reg, "gazpacho", None, None, None)
    assert result.results == []
    assert len(result.suggestions) == 5


def test_limit_caps_results(reg: Registry) -> None:
    assert len(search_foods(reg, "dal", None, None, None, limit=3).results) == 3
    assert len(search_foods(reg, "dal", None, None, None).results) == 10


@pytest.mark.parametrize("limit", [0, -1, 51])
def test_limit_out_of_range_is_rejected(reg: Registry, limit: int) -> None:
    with pytest.raises(ValueError, match="limit must be 1 to 50"):
        search_foods(reg, "dal", None, None, None, limit=limit)


@pytest.mark.parametrize("query", ["", "   ", "!?", "()"])
def test_blank_query_is_rejected(reg: Registry, query: str) -> None:
    with pytest.raises(ValueError, match="query"):
        search_foods(reg, query, None, None, None)


@pytest.mark.parametrize(
    ("region", "diet", "kind", "label"),
    [("central", None, None, "region"), (None, "keto", None, "diet"), (None, None, "meal", "kind")],
)
def test_unknown_filter_value_is_rejected(
    reg: Registry, region: str | None, diet: str | None, kind: str | None, label: str
) -> None:
    with pytest.raises(ValueError, match=f"unknown {label}"):
        search_foods(reg, "dal", region, diet, kind)
