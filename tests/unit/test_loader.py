import os
import shutil
from pathlib import Path

import pytest

from nutrition_mcp.config import Settings
from nutrition_mcp.loader import (
    INGREDIENT_COLUMNS,
    DataError,
    build_registry,
    compute_dish,
    load_dataset,
)
from nutrition_mcp.models import RecipeItem
from nutrition_mcp.registry import UnknownFood

FIXTURE = Path(__file__).parent.parent / "fixtures" / "mini_dataset"

# These tests are about the loader, so they run against the fixture alone. The bundled data
# is checked in tests/integration/test_bundled_data.py.
pytestmark = pytest.mark.usefixtures("no_bundled_data")


def copy_fixture(tmp_path: Path, name: str = "dataset") -> Path:
    target = tmp_path / name
    shutil.copytree(FIXTURE, target)
    return target


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def rename_source_ids(folder: Path, suffix: str) -> None:
    # Lets a copy of the fixture load next to the original without clashing on source ids.
    for path in folder.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            for old in ("test_source", "test_recipes", "test_gi"):
                text = text.replace(old, f"{old}_{suffix}")
            path.write_text(text, encoding="utf-8")


def test_loads_fixture_dataset() -> None:
    sources, ingredients, dishes = load_dataset(FIXTURE)
    assert [s.id for s in sources] == ["test_source", "test_recipes", "test_gi"]
    assert [i.id for i in ingredients] == ["toor_dal", "water", "ghee"]
    assert [d.id for d in dishes] == ["test_dal"]
    toor = ingredients[0]
    assert toor.aliases == ["arhar dal", "tur dal"]
    assert toor.per_100g.kcal == 343
    assert toor.per_100g.vit_d_ug is None
    assert (toor.gi, toor.gi_ref) == (28, "test_gi: Toor dal (Table 1, T1-001)")
    assert ingredients[1].aliases == []
    assert ingredients[1].density_g_per_ml == 1


def test_ingredient_columns_match_the_documented_format() -> None:
    documented = (
        "id,name,aliases,diet,source_id,source_record,kcal,protein_g,fat_g,sat_fat_g,"
        "carbs_available_g,sugars_g,fibre_g,sodium_mg,iron_mg,calcium_mg,zinc_mg,folate_ug,"
        "vit_a_ug,vit_b12_ug,vit_c_mg,vit_d_ug,gi,gi_ref,density_g_per_ml"
    )
    assert tuple(documented.split(",")) == INGREDIENT_COLUMNS


def test_dish_per_100g_is_computed() -> None:
    dal = build_registry([FIXTURE]).get("test_dal")
    # 100 g toor dal at 343 kcal per 100 g, spread over a 400 g cooked weight.
    assert dal.per_100g.kcal == pytest.approx(85.75)
    assert dal.per_100g.protein_g == pytest.approx(5.5)
    assert dal.per_100g.folate_ug == pytest.approx(114)


def test_dish_food_fields() -> None:
    dal = build_registry([FIXTURE]).get("test_dal")
    assert dal.kind == "dish"
    assert dal.regions == ["south", "north"]
    assert dal.serving_g == 200
    assert dal.measures == {"katori": 150, "serving": 200}
    assert dal.source_ids == ["test_recipes", "test_source"]
    assert dal.assumptions == "No added fat. The dal is boiled in water only."
    assert dal.density_g_per_ml is None
    assert (dal.gi, dal.gi_ref) == (None, None)


def test_dish_keeps_a_serving_measure_it_defines(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    edit(folder / "dishes" / "test_dal.yaml", "  katori: 150", "  katori: 150\n  serving: 180")
    assert build_registry([folder]).get("test_dal").measures["serving"] == 180


def test_ingredient_food_fields() -> None:
    registry = build_registry([FIXTURE])
    toor = registry.get("toor_dal")
    assert toor.kind == "ingredient"
    assert toor.regions == []
    assert toor.incomplete == {}
    assert toor.measures == {}
    assert toor.serving_g is None
    assert toor.assumptions is None
    # The GI comes from another source than the nutrients, so both are listed.
    assert toor.source_ids == ["test_source", "test_gi"]
    assert (toor.gi, toor.gi_ref) == (28, "test_gi: Toor dal (Table 1, T1-001)")
    assert registry.get("ghee").density_g_per_ml == 0.91


def test_dish_gi_ref_adds_its_source(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    edit(
        folder / "dishes" / "test_dal.yaml",
        "source_id: test_recipes",
        'gi: 40\ngi_ref: "test_gi: Test dal (Table 1, T1-002)"\nsource_id: test_recipes',
    )
    dal = build_registry([folder]).get("test_dal")
    assert dal.gi == 40
    assert dal.source_ids == ["test_recipes", "test_source", "test_gi"]


def test_gi_ref_source_already_listed_is_not_repeated(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    edit(folder / "ingredients.csv", '"test_gi: ', '"test_source: ')
    assert build_registry([folder]).get("toor_dal").source_ids == ["test_source"]


def test_gi_ref_with_unknown_source_names_row(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "ingredients.csv"
    edit(path, '"test_gi: ', '"nowhere: ')
    with pytest.raises(DataError) as error:
        build_registry([folder])
    assert str(error.value).startswith(f"{path}:2: ")
    assert "nowhere" in str(error.value)


def test_dish_gi_ref_with_unknown_source_names_file(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "dishes" / "test_dal.yaml"
    edit(
        path,
        "source_id: test_recipes",
        'gi: 40\ngi_ref: "nowhere: Test dal (Table 1, T1-002)"\nsource_id: test_recipes',
    )
    with pytest.raises(DataError) as error:
        build_registry([folder])
    assert str(error.value).startswith(f"{path}:0: ")
    assert "nowhere" in str(error.value)


def test_gi_ref_without_a_source_id_names_row(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "ingredients.csv"
    edit(path, '"test_gi: Toor dal (Table 1, T1-001)"', "Toor dal (Table 1 T1-001)")
    with pytest.raises(DataError) as error:
        build_registry([folder])
    assert str(error.value).startswith(f"{path}:2: ")
    assert "gi_ref" in str(error.value)


def test_missing_nutrient_marks_incomplete() -> None:
    dal = build_registry([FIXTURE]).get("test_dal")
    assert dal.incomplete["vit_d_ug"] == ["toor_dal"]
    # Water reports 0 for vitamin D, so the dish value counts the known part only.
    assert dal.per_100g.vit_d_ug == 0
    assert set(dal.incomplete) == {"vit_d_ug", "vit_b12_ug"}


def test_nutrient_nobody_reports_is_none() -> None:
    dal = build_registry([FIXTURE]).get("test_dal")
    assert dal.per_100g.vit_b12_ug is None
    assert dal.incomplete["vit_b12_ug"] == ["toor_dal", "water"]


def test_compute_dish_names_a_repeated_ingredient_once() -> None:
    _, ingredients, dishes = load_dataset(FIXTURE)
    dish = dishes[0].model_copy(
        update={
            "ingredients": [
                RecipeItem(ingredient_id="toor_dal", grams=50),
                RecipeItem(ingredient_id="toor_dal", grams=50),
            ]
        }
    )
    _, incomplete = compute_dish(dish, {i.id: i for i in ingredients})
    assert incomplete["vit_d_ug"] == ["toor_dal"]


def test_unknown_ingredient_names_file(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    dish_file = folder / "dishes" / "test_dal.yaml"
    edit(dish_file, "ingredient_id: water", "ingredient_id: soda_water")
    with pytest.raises(DataError) as error:
        build_registry([folder])
    assert str(error.value).startswith(f"{dish_file}:0: ")
    assert "soda_water" in str(error.value)


def test_duplicate_id_across_datasets_fails(tmp_path: Path) -> None:
    first = copy_fixture(tmp_path, "first")
    second = copy_fixture(tmp_path, "second")
    rename_source_ids(second, "b")
    with pytest.raises(DataError) as error:
        build_registry([first, second])
    message = str(error.value)
    assert message.startswith(f"{second / 'ingredients.csv'}:2: ")
    assert "toor_dal" in message
    assert str(first / "ingredients.csv") in message


def test_duplicate_id_inside_one_dataset_fails(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    edit(folder / "ingredients.csv", "\nwater,Water", "\ntoor_dal,Water")
    with pytest.raises(DataError) as error:
        build_registry([folder])
    assert str(error.value).startswith(f"{folder / 'ingredients.csv'}:3: ")


def test_duplicate_source_id_across_datasets_fails(tmp_path: Path) -> None:
    first = copy_fixture(tmp_path, "first")
    second = copy_fixture(tmp_path, "second")
    with pytest.raises(DataError) as error:
        build_registry([first, second])
    message = str(error.value)
    assert message.startswith(f"{second / 'sources.yaml'}:0: ")
    assert "test_source" in message


def test_bad_csv_row_names_row(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "ingredients.csv"
    edit(path, "W1,0,0,0,0,0,0,0,4,0,3", "W1,0,0,0,0,0,0,0,4,-1,3")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{path}:3: ")
    assert "iron_mg" in str(error.value)


def test_non_numeric_cell_names_row(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    edit(folder / "ingredients.csv", "T1,343", "T1,lots")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{folder / 'ingredients.csv'}:2: ")
    assert "kcal" in str(error.value)


def test_empty_required_cell_names_row(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    edit(folder / "ingredients.csv", "water,Water,", "water,,")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{folder / 'ingredients.csv'}:3: ")
    assert "name" in str(error.value)


def test_row_with_wrong_cell_count_names_row(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    edit(folder / "ingredients.csv", "water,Water,,vegan,test_source,W1,0,0,0", "water,Water")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{folder / 'ingredients.csv'}:3: ")


def test_bom_and_crlf_load(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    for path in (folder / "ingredients.csv", folder / "dishes" / "test_dal.yaml"):
        text = path.read_text(encoding="utf-8")
        path.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    assert load_dataset(folder) == load_dataset(FIXTURE)


def test_unknown_column_fails_naming_it(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "ingredients.csv"
    edit(path, "vit_d_ug", "vitamin_d")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{path}:1: ")
    assert "vitamin_d" in str(error.value)


def test_missing_required_column_fails_naming_it(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "ingredients.csv"
    path.write_text(
        "id,name,aliases,diet,source_id,kcal\ntoor_dal,Toor dal,,vegan,test_source,343\n"
    )
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{path}:1: ")
    assert "source_record" in str(error.value)


def test_repeated_column_fails_naming_it(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "ingredients.csv"
    edit(path, "kcal,protein_g", "kcal,kcal")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert "kcal" in str(error.value)


def test_optional_columns_can_be_left_out(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    (folder / "ingredients.csv").write_text(
        "id,name,aliases,diet,source_id,source_record,kcal\n"
        "toor_dal,Toor dal,,vegan,test_source,T1,343\n"
    )
    _, ingredients, _ = load_dataset(folder)
    assert ingredients[0].per_100g.kcal == 343
    assert ingredients[0].per_100g.protein_g is None


def test_empty_csv_names_file(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "ingredients.csv"
    path.write_text("")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{path}:0: ")


def test_missing_file_names_path(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "ingredients.csv"
    path.unlink()
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{path}:0: ")


def test_missing_dishes_folder_names_path(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    shutil.rmtree(folder / "dishes")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{folder / 'dishes'}:0: ")


def test_file_that_is_not_utf8_names_file(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "ingredients.csv"
    path.write_bytes(b"id,name\n\xff\xfe")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{path}:0: ")
    assert "UTF-8" in str(error.value)


def test_unknown_source_id_on_ingredient_names_row(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "ingredients.csv"
    edit(path, "desi ghee,veg,test_source", "desi ghee,veg,nowhere")
    with pytest.raises(DataError) as error:
        build_registry([folder])
    assert str(error.value).startswith(f"{path}:4: ")
    assert "nowhere" in str(error.value)


def test_unknown_source_id_on_dish_names_file(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "dishes" / "test_dal.yaml"
    edit(path, "source_id: test_recipes", "source_id: nowhere")
    with pytest.raises(DataError) as error:
        build_registry([folder])
    assert str(error.value).startswith(f"{path}:0: ")
    assert "nowhere" in str(error.value)


def test_source_in_another_folder_resolves(tmp_path: Path) -> None:
    only_sources = tmp_path / "sources_only"
    (only_sources / "dishes").mkdir(parents=True)
    shutil.copy(FIXTURE / "sources.yaml", only_sources / "sources.yaml")
    header = (FIXTURE / "ingredients.csv").read_text().splitlines()[0]
    (only_sources / "ingredients.csv").write_text(header + "\n")
    uses_them = copy_fixture(tmp_path, "uses_them")
    (uses_them / "sources.yaml").write_text("[]\n")
    registry = build_registry([only_sources, uses_them])
    assert registry.get("test_dal").source_ids == ["test_recipes", "test_source"]


def test_unknown_key_in_dish_names_file_and_field(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "dishes" / "test_dal.yaml"
    edit(path, "servings: 2", "servings: 2\ncalories: 5")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{path}:0: ")
    assert "calories" in str(error.value)


def test_invalid_yaml_names_file_and_line(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "dishes" / "broken.yaml"
    path.write_text("id: ok\nname: [unclosed\n")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{path}:")
    assert "invalid YAML" in str(error.value)


def test_impossible_yaml_date_names_file(tmp_path: Path) -> None:
    # PyYAML raises a bare ValueError for a date that matches the format but does not exist.
    folder = copy_fixture(tmp_path)
    path = folder / "sources.yaml"
    edit(path, "retrieved: 2026-10-19", "retrieved: 2026-13-45")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{path}:")
    assert "invalid YAML" in str(error.value)


def test_sources_file_must_be_a_list(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "sources.yaml"
    path.write_text("id: test_source\n")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{path}:0: ")


def test_bad_source_entry_names_entry(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "sources.yaml"
    edit(path, "  url: https://example.org/loader-test-recipes\n", "")
    with pytest.raises(DataError) as error:
        load_dataset(folder)
    assert str(error.value).startswith(f"{path}:0: ")
    assert "entry 2" in str(error.value)
    assert "url" in str(error.value)


def test_registry_lists_foods_and_sources_in_load_order() -> None:
    registry = build_registry([FIXTURE])
    assert [f.id for f in registry.foods()] == ["toor_dal", "water", "ghee", "test_dal"]
    assert [s.id for s in registry.sources()] == ["test_source", "test_recipes", "test_gi"]


def test_registry_get_unknown_food_raises() -> None:
    with pytest.raises(UnknownFood):
        build_registry([FIXTURE]).get("no_such_food")
    assert issubclass(UnknownFood, KeyError)


def test_data_dirs_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NUTRITION_MCP_DATA_DIRS", os.pathsep.join(["/a", "/b"]))
    assert Settings().data_dirs == [Path("/a"), Path("/b")]


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATA_DIRS", "DEFAULT_KATORI_G", "LOG_LEVEL"):
        monkeypatch.delenv(f"NUTRITION_MCP_{name}", raising=False)
    settings = Settings()
    assert settings.data_dirs == []
    assert settings.default_katori_g == 150.0
    assert settings.log_level == "INFO"
