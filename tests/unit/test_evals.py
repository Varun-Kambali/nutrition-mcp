import json
from pathlib import Path
from typing import Any

import pytest
import run_evals

from nutrition_mcp import loader

NUTRIENTS = ("kcal", "protein_g", "carbs_available_g", "iron_mg", "calcium_mg")

SOURCES_YAML = """\
- id: cofid_2021
  name: Test CoFID
  url: https://example.org/cofid
  license: CC0-1.0
  version: "1"
  retrieved: 2026-10-20
  attribution: Synthetic values for the evaluation tests.
- id: usda_fdc
  name: Test USDA
  url: https://example.org/usda
  license: CC0-1.0
  version: "1"
  retrieved: 2026-10-20
  attribution: Synthetic values for the evaluation tests.
- id: test_recipes
  name: Test recipes
  url: https://example.org/recipes
  license: CC0-1.0
  version: "1"
  retrieved: 2026-10-20
  attribution: Synthetic recipes for the evaluation tests.
"""


def _dish(dish_id: str, items: list[tuple[str, float]], cooked_weight_g: float) -> str:
    lines = [
        f"id: {dish_id}",
        f"name: {dish_id}",
        "aliases: []",
        "regions: [south]",
        "diet: vegan",
        "servings: 1",
        f"cooked_weight_g: {cooked_weight_g}",
        "ingredients:",
    ]
    for ingredient_id, grams in items:
        lines += [f"  - ingredient_id: {ingredient_id}", f"    grams: {grams}"]
    lines += ["measures: {}", "assumptions: No added fat.", "source_id: test_recipes"]
    return "\n".join(lines) + "\n"


@pytest.fixture
def tiny_dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A three-ingredient, two-dish dataset standing in for the bundled data.

    dal_a uses only CoFID ingredients; dal_b mixes in a USDA one that has no iron value, so
    its iron is flagged incomplete.
    """
    folder = tmp_path / "bundled"
    (folder / "dishes").mkdir(parents=True)
    (folder / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")
    header = ",".join(loader.INGREDIENT_COLUMNS)
    blank = {column: "" for column in loader.INGREDIENT_COLUMNS}

    def row(**cells: str) -> str:
        return ",".join({**blank, **cells}[column] for column in loader.INGREDIENT_COLUMNS)

    rows = [
        row(id="dal", name="Dal", diet="vegan", source_id="cofid_2021", source_record="C1",
            kcal="300", protein_g="20", carbs_available_g="50", iron_mg="5", calcium_mg="100"),
        row(id="water", name="Water", diet="vegan", source_id="cofid_2021", source_record="C2",
            kcal="0", protein_g="0", carbs_available_g="0", iron_mg="0", calcium_mg="0"),
        row(id="oil", name="Oil", diet="vegan", source_id="usda_fdc", source_record="U1",
            kcal="900", protein_g="0", carbs_available_g="0", calcium_mg="0"),
    ]  # fmt: skip
    (folder / "ingredients.csv").write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    (folder / "dishes" / "dal_a.yaml").write_text(
        _dish("dal_a", [("dal", 100), ("water", 300)], 400), encoding="utf-8"
    )
    (folder / "dishes" / "dal_b.yaml").write_text(
        _dish("dal_b", [("dal", 100), ("oil", 10), ("water", 290)], 400), encoding="utf-8"
    )
    monkeypatch.setattr(loader, "bundled_dir", lambda: folder)
    return folder


@pytest.fixture
def references_csv(tmp_path: Path) -> Path:
    # dal_a computes to 75 kcal, 5 g protein, 12.5 g carbs, 1.25 mg iron, 25 mg calcium per
    # 100 g; dal_b to 97.5 kcal and the same protein and carbs, with iron incomplete.
    path = tmp_path / "references.csv"
    path.write_text(
        "dish_id,source_id,record,description,kcal,protein_g,carbs_available_g,iron_mg,calcium_mg\n"
        'dal_a,cofid_2021,R1,"Dal, homemade",100,5,10,1,\n'
        'dal_b,usda_fdc,R2,"Dal [Survey (FNDDS)]",75,4,0,2,20\n',
        encoding="utf-8",
    )
    return path


def test_mape() -> None:
    assert run_evals.mape([100, 200], [110, 180]) == pytest.approx(10.0)


def test_mape_needs_values() -> None:
    with pytest.raises(ValueError):
        run_evals.mape([], [])


def _row(dish_id: str, reference: float | None, computed: float | None, incomplete: bool = False):
    values: dict[str, Any] = {key: None for key in NUTRIENTS}
    return {
        "dish_id": dish_id,
        "reference": {**values, "kcal": reference},
        "computed": {**values, "kcal": computed},
        "incomplete": ["kcal"] if incomplete else [],
    }


def test_score_leaves_out_missing_zero_and_incomplete_values() -> None:
    rows = [
        _row("kept", 100, 110),
        _row("kept_too", 200, 150),
        _row("no_reference", None, 50),
        _row("zero_reference", 0, 50),
        _row("no_computed", 100, None),
        _row("incomplete", 100, 300, incomplete=True),
    ]
    kcal = run_evals.score(rows)["kcal"]
    assert kcal["n"] == 2
    assert kcal["mape"] == pytest.approx(17.5)
    assert [worst["dish_id"] for worst in kcal["worst"]] == ["kept_too", "kept"]
    assert kcal["worst"][0] == {
        "dish_id": "kept_too",
        "reference": 200,
        "computed": 150,
        "ape": 25.0,
    }


def test_score_with_no_usable_rows_has_no_mape() -> None:
    assert run_evals.score([_row("only", None, 50)])["kcal"] == {"n": 0, "mape": None, "worst": []}


def test_worst_lists_at_most_three_rows() -> None:
    rows = [_row(f"dish_{ape}", 100, 100 + ape) for ape in (1, 5, 3, 9)]
    worst = run_evals.score(rows)["kcal"]["worst"]
    assert [entry["dish_id"] for entry in worst] == ["dish_9", "dish_5", "dish_3"]


THRESHOLDS = {
    "kcal": {"max_mape": 25.0, "min_n": 18},
    "protein_g": {"max_mape": 20.0, "min_n": 18},
    "carbs_available_g": {"max_mape": 20.0, "min_n": 18},
}


def _per_nutrient(kcal_mape: float | None, kcal_n: int) -> dict[str, Any]:
    return {
        "kcal": {"n": kcal_n, "mape": kcal_mape, "worst": []},
        "protein_g": {"n": 18, "mape": 12.0, "worst": []},
        "carbs_available_g": {"n": 18, "mape": 15.0, "worst": []},
        # Reported only, so a large error here never fails the check.
        "iron_mg": {"n": 18, "mape": 80.0, "worst": []},
    }


def test_threshold_check_fails_on_regression() -> None:
    assert run_evals.check_thresholds(_per_nutrient(25.0, 18), THRESHOLDS) == []

    worse = run_evals.check_thresholds(_per_nutrient(25.1, 18), THRESHOLDS)
    assert len(worse) == 1
    assert "kcal" in worse[0] and "25.1" in worse[0] and "25.0" in worse[0]

    fewer = run_evals.check_thresholds(_per_nutrient(10.0, 17), THRESHOLDS)
    assert len(fewer) == 1
    assert "kcal" in fewer[0] and "17" in fewer[0] and "18" in fewer[0]

    # With no usable rows there is no MAPE, and n alone fails the check.
    empty = run_evals.check_thresholds(_per_nutrient(None, 0), THRESHOLDS)
    assert len(empty) == 1 and "kcal" in empty[0]


def test_threshold_check_fails_when_a_gated_nutrient_has_no_threshold() -> None:
    failures = run_evals.check_thresholds(_per_nutrient(10.0, 18), {"kcal": THRESHOLDS["kcal"]})
    assert any("protein_g" in failure for failure in failures)


def test_dataset_version_is_stable_and_follows_the_data(tiny_dataset: Path) -> None:
    version = run_evals.dataset_version(tiny_dataset)
    assert len(version) == 12
    assert all(char in "0123456789abcdef" for char in version)
    assert run_evals.dataset_version(tiny_dataset) == version

    # Files the server does not load leave the version alone.
    (tiny_dataset / "LICENSE.md").write_text("license text\n", encoding="utf-8")
    assert run_evals.dataset_version(tiny_dataset) == version

    dish = tiny_dataset / "dishes" / "dal_a.yaml"
    dish.write_text(dish.read_text(encoding="utf-8").replace("400", "450"), encoding="utf-8")
    assert run_evals.dataset_version(tiny_dataset) != version


def test_data_accuracy_results_shape(tiny_dataset: Path, references_csv: Path) -> None:
    result = run_evals.data_accuracy(references_csv, date="2026-10-20", commit="abc1234")

    assert result["suite"] == "data_accuracy"
    assert result["date"] == "2026-10-20"
    assert result["commit"] == "abc1234"
    assert result["dataset_version"] == run_evals.dataset_version(tiny_dataset)
    assert result["n"] == 2
    assert set(result["per_nutrient"]) == set(NUTRIENTS)

    kcal = result["per_nutrient"]["kcal"]
    assert kcal["n"] == 2
    # dal_a: |75 - 100| / 100 = 25%; dal_b: |97.5 - 75| / 75 = 30%.
    assert kcal["mape"] == pytest.approx(27.5)
    assert [entry["dish_id"] for entry in kcal["worst"]] == ["dal_b", "dal_a"]
    # dal_b's carbs reference is 0, dal_a's calcium is missing and dal_b's iron is incomplete.
    assert result["per_nutrient"]["carbs_available_g"]["n"] == 1
    assert result["per_nutrient"]["calcium_mg"]["n"] == 1
    assert result["per_nutrient"]["iron_mg"]["n"] == 1

    rows = {row["dish_id"]: row for row in result["rows"]}
    assert rows["dal_a"]["source_id"] == "cofid_2021"
    assert rows["dal_a"]["record"] == "R1"
    assert rows["dal_a"]["reference"]["calcium_mg"] is None
    assert rows["dal_a"]["computed"] == pytest.approx(
        {"kcal": 75, "protein_g": 5, "carbs_available_g": 12.5, "iron_mg": 1.25, "calcium_mg": 25}
    )
    assert rows["dal_a"]["same_source_ingredients"] is True
    assert rows["dal_b"]["same_source_ingredients"] is False
    assert rows["dal_b"]["ingredient_source_ids"] == ["cofid_2021", "usda_fdc"]
    assert rows["dal_b"]["incomplete"] == ["iron_mg"]

    # The results file is plain JSON.
    assert json.loads(json.dumps(result)) == result


def test_a_reference_to_an_unknown_dish_is_an_error(tiny_dataset: Path, tmp_path: Path) -> None:
    path = tmp_path / "references.csv"
    path.write_text(
        "dish_id,source_id,record,description,kcal,protein_g,carbs_available_g,iron_mg,calcium_mg\n"
        "no_such_dish,cofid_2021,R1,Dal,100,5,10,1,20\n",
        encoding="utf-8",
    )
    with pytest.raises(run_evals.EvalError, match="no_such_dish"):
        run_evals.data_accuracy(path, date="2026-10-20", commit="abc1234")


def test_references_need_the_expected_header(tiny_dataset: Path, tmp_path: Path) -> None:
    path = tmp_path / "references.csv"
    path.write_text("dish_id,kcal\ndal_a,100\n", encoding="utf-8")
    with pytest.raises(run_evals.EvalError, match="header"):
        run_evals.data_accuracy(path, date="2026-10-20", commit="abc1234")


def test_results_file_is_named_by_date_suite_and_commit(
    tiny_dataset: Path, references_csv: Path, tmp_path: Path
) -> None:
    result = run_evals.data_accuracy(references_csv, date="2026-10-20", commit="abc1234")
    path = run_evals.write_results(result, tmp_path / "results")
    assert path.name == "2026-10-20_data-accuracy_abc1234.json"
    assert json.loads(path.read_text(encoding="utf-8")) == result


def _grounding_file(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    fake = {
        "suite": "grounding",
        "date": "2026-10-19",
        "generated_at": "2026-10-19T10:00:00+00:00",
        "commit": "fff0000",
        "dataset_version": "0123456789ab",
        "summary": [{"measure": "kcal MAPE with tools (%)", "n": 30, "value": 4.2}],
    }
    (folder / "2026-10-19_test-model_fff0000.json").write_text(json.dumps(fake), encoding="utf-8")


def test_summary_keeps_the_grounding_rows_when_data_accuracy_rewrites_it(
    tiny_dataset: Path, references_csv: Path, tmp_path: Path
) -> None:
    out = tmp_path / "results"
    _grounding_file(out)
    result = run_evals.data_accuracy(references_csv, date="2026-10-20", commit="abc1234")
    run_evals.write_results(result, out)

    summary = run_evals.write_summary(out).read_text(encoding="utf-8")

    lines = [line for line in summary.splitlines() if line.startswith("| ")]
    grounding = [line for line in lines if "kcal MAPE with tools" in line]
    accuracy = [line for line in lines if "abc1234" in line]
    assert len(grounding) == 1 and "| 30 | 4.2 |" in grounding[0] and "fff0000" in grounding[0]
    assert len(accuracy) == len(NUTRIENTS)
    # Grounding first, then data accuracy.
    assert lines.index(grounding[0]) < lines.index(accuracy[0])
    kcal = next(line for line in accuracy if "kcal" in line)
    assert "| 2 | 27.5 |" in kcal
    assert result["dataset_version"] in kcal and "2026-10-20" in kcal


def test_summary_uses_the_latest_results_file_of_a_suite(
    tiny_dataset: Path, references_csv: Path, tmp_path: Path
) -> None:
    out = tmp_path / "results"
    older = run_evals.data_accuracy(references_csv, date="2026-10-20", commit="aaa1111")
    older["generated_at"] = "2026-10-20T08:00:00+00:00"
    newer = run_evals.data_accuracy(references_csv, date="2026-10-20", commit="bbb2222")
    newer["generated_at"] = "2026-10-20T09:00:00+00:00"
    run_evals.write_results(newer, out)
    run_evals.write_results(older, out)

    summary = run_evals.write_summary(out).read_text(encoding="utf-8")
    assert "bbb2222" in summary and "aaa1111" not in summary


def test_summary_names_a_results_file_it_cannot_use(tmp_path: Path) -> None:
    out = tmp_path / "results"
    out.mkdir()
    (out / "2026-10-20_broken.json").write_text('{"suite": "grounding"}', encoding="utf-8")
    with pytest.raises(run_evals.EvalError, match=r"2026-10-20_broken\.json: missing date"):
        run_evals.write_summary(out)


def test_check_mode_writes_nothing_and_exits_by_the_thresholds(
    tiny_dataset: Path,
    references_csv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restore_logging: None,
) -> None:
    monkeypatch.setattr(run_evals, "REFERENCES", references_csv)
    out = tmp_path / "results"
    loose = tmp_path / "loose.toml"
    loose.write_text(
        "[data_accuracy.kcal]\nmax_mape = 30.0\nmin_n = 2\n"
        "[data_accuracy.protein_g]\nmax_mape = 30.0\nmin_n = 2\n"
        "[data_accuracy.carbs_available_g]\nmax_mape = 30.0\nmin_n = 1\n",
        encoding="utf-8",
    )
    tight = tmp_path / "tight.toml"
    tight.write_text(
        loose.read_text(encoding="utf-8").replace("max_mape = 30.0", "max_mape = 1.0", 1),
        encoding="utf-8",
    )
    args = ["--suite", "data_accuracy", "--out", str(out), "--check"]

    assert run_evals.main([*args, str(loose)]) == 0
    assert "FAIL" not in capsys.readouterr().out
    assert run_evals.main([*args, str(tight)]) == 1
    report = capsys.readouterr().out
    assert "FAIL kcal: MAPE 27.5 is above max_mape 1.0" in report
    assert "FAIL protein_g" not in report
    assert not out.exists()


def test_a_normal_run_writes_the_results_file_and_summary(
    tiny_dataset: Path,
    references_csv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_logging: None,
) -> None:
    monkeypatch.setattr(run_evals, "REFERENCES", references_csv)
    out = tmp_path / "results"
    assert run_evals.main(["--suite", "data_accuracy", "--out", str(out)]) == 0
    names = sorted(path.name for path in out.iterdir())
    assert len(names) == 2 and names[1] == "summary.md"
    assert "_data-accuracy_" in names[0]


def test_grounding_is_not_implemented_yet(
    capsys: pytest.CaptureFixture[str], restore_logging: None
) -> None:
    assert run_evals.main(["--suite", "grounding"]) == 2
    assert "not implemented yet" in capsys.readouterr().err
