"""Run an evaluation suite and keep evals/results/summary.md current.

Usage:
  uv run python evals/run_evals.py --suite data_accuracy
  uv run python evals/run_evals.py --suite data_accuracy --check evals/thresholds.toml
  uv run --group eval python evals/run_evals.py --suite grounding [--limit N]

`data_accuracy` compares the dish values the server computes with independent records of the
same dishes as eaten (evals/datasets/references.csv, built by scripts/build_references.py). It
makes no model calls. A normal run writes evals/results/YYYY-MM-DD_data-accuracy_<commit>.json
and rebuilds summary.md. With --check it compares the result with a thresholds file, writes
nothing, and exits 1 when a gated nutrient fails; CI runs it that way on every pull request.

`grounding` asks a Gemini model each question in evals/datasets/questions.jsonl from memory
and again with this server's tools (evals/grounding.py). It needs GEMINI_API_KEY and calls the
model, so it runs on demand only (`make eval`, which loads .env). EVAL_MODEL overrides the
default model. A run writes evals/results/YYYY-MM-DD_<model>_<commit>.json and rebuilds
summary.md; --limit N asks only the first N questions.

Every results file is JSON with `suite`, `date`, `generated_at`, `commit` and
`dataset_version`. summary.md is rebuilt from the newest file of each suite (the latest
`generated_at`), grounding first. A grounding file also names its `model` and lists its
summary rows under `summary`, each with `measure`, `n`, `without_tools` and `with_tools`.

The report goes to stdout as plain text; logs go to stderr as JSON.
"""

import argparse
import csv
import functools
import hashlib
import json
import logging
import math
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import grounding

from nutrition_mcp import loader
from nutrition_mcp.log import configure_logging
from nutrition_mcp.models import Dish, Ingredient
from nutrition_mcp.nutrients import NUTRIENTS, REFERENCE_NUTRIENTS
from nutrition_mcp.registry import Registry, UnknownFood

ROOT = Path(__file__).resolve().parent.parent
REFERENCES = ROOT / "evals" / "datasets" / "references.csv"
RESULTS = ROOT / "evals" / "results"
SUMMARY = "summary.md"

# CI fails when a gated nutrient gets worse. Iron and calcium are reported only: fortified
# flour and enriched rice in the reference foods make large gaps there that say little about
# the recipes.
EVALUATED = REFERENCE_NUTRIENTS
GATED = ("kcal", "protein_g", "carbs_available_g")
REPORTED = tuple(key for key in EVALUATED if key not in GATED)
REFERENCE_COLUMNS = ["dish_id", "source_id", "record", "description", *EVALUATED]
REFERENCE_SOURCES = frozenset({"usda_fdc", "cofid_2021"})
COFID = "cofid_2021"
WORST = 3
SUITES = ("data_accuracy", "grounding")
# Every results file has these, whatever its suite; summary.md is built from them.
RESULT_FIELDS = ("suite", "date", "generated_at", "commit", "dataset_version")
LABELS = {spec.key: f"{spec.label} ({spec.unit})" for spec in NUTRIENTS}
API_KEY_VAR = "GEMINI_API_KEY"
MODEL_VAR = "EVAL_MODEL"

log = logging.getLogger("nutrition_mcp.evals")


class EvalError(Exception):
    """A problem with an input file. The message names the file."""


@dataclass(frozen=True, slots=True)
class Reference:
    dish_id: str
    source_id: str
    record: str
    values: dict[str, float | None]


def _ape(reference: float, computed: float) -> float:
    if reference == 0:
        raise ValueError("a reference value of 0 has no percentage error")
    return abs(computed - reference) / reference * 100


def mape(reference: Sequence[float], computed: Sequence[float]) -> float:
    """Mean absolute percentage error of `computed` against `reference`, in percent.

    The reference comes first because each error is a share of the reference value.
    """
    if not reference or len(reference) != len(computed):
        raise ValueError("mape needs two non-empty lists of the same length")
    errors = [_ape(ref, value) for ref, value in zip(reference, computed, strict=True)]
    return sum(errors) / len(errors)


def score(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """n, MAPE and the worst rows for each evaluated nutrient.

    A row counts for a nutrient only when its reference value is present and above 0 and its
    computed value is present and complete. A computed value with an ingredient value missing
    is a floor, so comparing it would blame the recipe for a gap in the source data.
    """
    scores: dict[str, dict[str, Any]] = {}
    for key in EVALUATED:
        pairs = [
            (row["dish_id"], row["reference"][key], row["computed"][key])
            for row in rows
            if row["reference"][key] is not None
            and row["reference"][key] > 0
            and row["computed"][key] is not None
            and key not in row["incomplete"]
        ]
        if not pairs:
            scores[key] = {"n": 0, "mape": None, "worst": []}
            continue
        errors = sorted(
            ((_ape(ref, value), dish_id, ref, value) for dish_id, ref, value in pairs),
            key=lambda error: (-error[0], error[1]),
        )
        scores[key] = {
            "n": len(pairs),
            "mape": round(mape([ref for _, ref, _ in pairs], [value for _, _, value in pairs]), 1),
            "worst": [
                {"dish_id": dish_id, "reference": ref, "computed": value, "ape": round(ape, 1)}
                for ape, dish_id, ref, value in errors[:WORST]
            ],
        }
    return scores


def _value(text: str, path: Path, line: int, column: str) -> float | None:
    if not text.strip():
        return None
    try:
        value = float(text)
    except ValueError:
        raise EvalError(f"{path}:{line}: {column} is not a number: {text!r}") from None
    if value < 0:
        raise EvalError(f"{path}:{line}: {column} is negative")
    return value


def load_references(path: Path) -> list[Reference]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != REFERENCE_COLUMNS:
                raise EvalError(f"{path}: header must be {','.join(REFERENCE_COLUMNS)}")
            rows = list(reader)
    except OSError as err:
        raise EvalError(f"{path}: cannot read file: {err.strerror or err}") from err
    references: list[Reference] = []
    seen: set[str] = set()
    for line, row in enumerate(rows, start=2):
        dish_id = row["dish_id"].strip()
        # Each dish counts once, so one dish cannot weigh twice in a MAPE.
        if dish_id in seen:
            raise EvalError(f"{path}:{line}: second reference for {dish_id!r}")
        seen.add(dish_id)
        if row["source_id"] not in REFERENCE_SOURCES:
            raise EvalError(
                f"{path}:{line}: source_id must be one of {', '.join(sorted(REFERENCE_SOURCES))}"
            )
        values = {key: _value(row[key], path, line, key) for key in EVALUATED}
        references.append(Reference(dish_id, row["source_id"], row["record"].strip(), values))
    return references


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def compare(
    references: Sequence[Reference],
    registry: Registry,
    ingredient_sources: Mapping[str, Sequence[str]],
) -> list[dict[str, Any]]:
    """One row per reference: its values next to the dish values the server computes.

    `ingredient_sources` maps each dish to the source IDs of its ingredients. They give the
    energy basis of the computed side: USDA kcal counts fibre and CoFID kcal does not.
    """
    rows: list[dict[str, Any]] = []
    for ref in references:
        try:
            food = registry.get(ref.dish_id)
        except UnknownFood:
            raise EvalError(f"references name {ref.dish_id!r}, which is not a dish") from None
        if food.kind != "dish":
            raise EvalError(f"references name {ref.dish_id!r}, which is not a dish")
        sources = sorted(set(ingredient_sources[ref.dish_id]))
        rows.append(
            {
                "dish_id": ref.dish_id,
                "source_id": ref.source_id,
                "record": ref.record,
                "reference": dict(ref.values),
                "computed": {key: _round(food.per_100g.get(key)) for key in EVALUATED},
                "incomplete": [key for key in EVALUATED if key in food.incomplete],
                "ingredient_source_ids": sources,
                # Both sides from CoFID: the comparison still tests the recipe (amounts,
                # cooked weight) but not the ingredient values.
                "same_source_ingredients": ref.source_id == COFID and sources == [COFID],
            }
        )
    return rows


def dataset_version(folder: Path) -> str:
    """First 12 hex characters of SHA-256 over the files the loader reads, in path order."""
    files = [folder / "sources.yaml", folder / "ingredients.csv", *folder.glob("dishes/*.yaml")]
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda path: path.relative_to(folder).as_posix()):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _ingredient_sources(
    dishes: Sequence[Dish], ingredients: Sequence[Ingredient]
) -> dict[str, list[str]]:
    source_of = {ingredient.id: ingredient.source_id for ingredient in ingredients}
    return {
        dish.id: [source_of[item.ingredient_id] for item in dish.ingredients] for dish in dishes
    }


def data_accuracy(references_path: Path, *, now: datetime, commit: str) -> dict[str, Any]:
    """Score the bundled dataset, the one the server loads, against the reference file.

    `date` and `generated_at` both come from `now` in UTC, so they always name the same day.
    """
    utc = now.astimezone(UTC)
    folder = loader.bundled_dir()
    registry = loader.build_registry([])
    _, ingredients, dishes = loader.load_dataset(folder)
    rows = compare(
        load_references(references_path), registry, _ingredient_sources(dishes, ingredients)
    )
    return {
        "suite": "data_accuracy",
        "date": utc.date().isoformat(),
        "generated_at": utc.isoformat(timespec="seconds"),
        "commit": commit,
        "dataset_version": dataset_version(folder),
        "n": len(rows),
        "per_nutrient": score(rows),
        "rows": rows,
    }


def load_thresholds(path: Path) -> dict[str, dict[str, float]]:
    try:
        with path.open("rb") as handle:
            table = tomllib.load(handle).get("data_accuracy")
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise EvalError(f"{path}: {err}") from err
    if not isinstance(table, dict):
        raise EvalError(f"{path}: expected a [data_accuracy.<nutrient>] table per nutrient")
    for key, limit in table.items():
        if not isinstance(limit, dict) or set(limit) != {"max_mape", "min_n"}:
            raise EvalError(f"{path}: [data_accuracy.{key}] needs max_mape and min_n, only")
        max_mape, min_n = limit["max_mape"], limit["min_n"]
        # bool is an int in Python, and `true` would otherwise pass as min_n = 1. TOML also
        # reads nan and inf, and either one would let every MAPE through.
        if (
            isinstance(max_mape, bool)
            or not isinstance(max_mape, int | float)
            or not math.isfinite(max_mape)
            or max_mape < 0
        ):
            raise EvalError(
                f"{path}: [data_accuracy.{key}] max_mape must be a finite number, 0 or more"
            )
        if isinstance(min_n, bool) or not isinstance(min_n, int) or min_n < 0:
            raise EvalError(
                f"{path}: [data_accuracy.{key}] min_n must be a whole number, 0 or more"
            )
    return table


def check_thresholds(
    per_nutrient: Mapping[str, Mapping[str, Any]], thresholds: Mapping[str, Mapping[str, float]]
) -> list[str]:
    """One message per failure, empty when every gated nutrient passes.

    A nutrient fails when its MAPE is above max_mape, or when fewer rows than min_n were
    usable: losing references would otherwise make a worse result look better.
    """
    failures: list[str] = []
    for key in GATED:
        limit = thresholds.get(key)
        if limit is None:
            failures.append(f"{key}: the thresholds file has no entry for it")
            continue
        scored = per_nutrient.get(key, {"n": 0, "mape": None})
        if scored["n"] < limit["min_n"]:
            failures.append(f"{key}: n {scored['n']} is below min_n {limit['min_n']}")
        if scored["mape"] is not None and scored["mape"] > limit["max_mape"]:
            failures.append(f"{key}: MAPE {scored['mape']} is above max_mape {limit['max_mape']}")
    return failures


def _file_label(result: Mapping[str, Any]) -> str:
    if result["suite"] == "grounding":
        # A model id can hold a "/" or a space, which have no place in a file name.
        return re.sub(r"[^A-Za-z0-9._-]+", "-", str(result["model"]))
    return str(result["suite"]).replace("_", "-")


def write_results(result: Mapping[str, Any], out: Path) -> Path:
    """Write a results file named by date, then suite (or model, for grounding), then commit."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result['date']}_{_file_label(result)}_{result['commit']}.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return path


def _latest_results(out: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    latest: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(out.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            raise EvalError(f"{path}: not valid JSON: {err}") from err
        missing = [field for field in RESULT_FIELDS if field not in data]
        if missing:
            raise EvalError(f"{path}: missing {', '.join(missing)}")
        suite = data["suite"]
        if suite not in SUITES:
            raise EvalError(f"{path}: suite must be one of {', '.join(SUITES)}")
        # ISO 8601 timestamps in UTC sort in time order as strings.
        if suite not in latest or data["generated_at"] >= latest[suite][1]["generated_at"]:
            latest[suite] = (path, data)
    return latest


def _cell(value: Any) -> str:
    # json.dumps writes a number the way the results file holds it.
    return "n/a" if value is None else json.dumps(value)


def _summary_rows(path: Path, data: Mapping[str, Any]) -> list[tuple[str, Any, Any]]:
    try:
        rows = []
        for key in EVALUATED:
            scored = data["per_nutrient"][key]
            gate = "gated" if key in GATED else "reported"
            rows.append((f"{LABELS[key]} MAPE (%), {gate}", scored["n"], scored["mape"]))
        return rows
    except KeyError as err:
        raise EvalError(f"{path}: missing {err.args[0]!r}, needed for summary.md") from None
    except TypeError:
        raise EvalError(f"{path}: summary data has the wrong shape for summary.md") from None


def _grounding_lines(path: Path, data: Mapping[str, Any]) -> list[str]:
    """The grounding table, one row per summary measure, the headline first.

    The value columns are named Baseline and This project, as in the README template: the
    portfolio site reads the first row of the first table in this file and shows it as
    "measure: baseline -> this project".
    """
    try:
        cells = [
            (row["measure"], row["without_tools"], row["with_tools"], row["n"])
            for row in data["summary"]
        ]
        shared = (
            f"| {data['model']} | {data['date']} | {data['commit']} | {data['dataset_version']} "
            f"| [{path.name}]({path.name}) |"
        )
    except KeyError as err:
        raise EvalError(f"{path}: missing {err.args[0]!r}, needed for summary.md") from None
    except TypeError:
        raise EvalError(f"{path}: summary data has the wrong shape for summary.md") from None
    lines = [
        "Grounding asks a model each question in `evals/datasets/questions.jsonl` twice with the",
        "same prompt. Baseline is the model answering from memory; This project is the same",
        "model calling this server's tools over stdio. Golden answers are computed by the",
        "server's own query functions from the bundled data. An error is the mean absolute",
        "percentage error over the answers that gave numbers; an answer without numbers is left",
        "out of it and counts as outside 10%. Shares run from 0 to 1. n is the number of",
        "questions behind a row, or of tool calls for latency.",
        "",
        "| Measure | Baseline | This project | n | Model | Date | Commit | Dataset version "
        "| Results file |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for measure, without, with_, n in cells:
        lines.append(f"| {measure} | {_cell(without)} | {_cell(with_)} | {_cell(n)} {shared}")
    return [*lines, ""]


def write_summary(out: Path) -> Path:
    """Rebuild summary.md from the newest results file of each suite, grounding first."""
    latest = _latest_results(out)
    lines = [
        "# Evaluation results",
        "",
        "Rebuilt by `evals/run_evals.py` from the newest results file of each suite. Do not",
        "edit by hand. Each number is copied from the results file named on its row.",
        "",
    ]
    if "grounding" in latest:
        lines += _grounding_lines(*latest["grounding"])
    lines += [
        "Data accuracy compares the values computed for each bundled dish with an independent",
        "record of the same dish (`evals/datasets/references.csv`). n is the number of dishes",
        "used for a nutrient, and the value is the mean absolute percentage error against the",
        "reference. A gated nutrient fails CI when its MAPE is above, or its n below, the limits",
        "in `evals/thresholds.toml`.",
        "",
        "| Suite | Measure | n | Value | Date | Commit | Dataset version | Results file |",
        "|---|---|---|---|---|---|---|---|",
    ]
    if "data_accuracy" in latest:
        path, data = latest["data_accuracy"]
        for measure, n, value in _summary_rows(path, data):
            lines.append(
                f"| data accuracy | {measure} | {_cell(n)} | {_cell(value)} "
                f"| {data['date']} | {data['commit']} | {data['dataset_version']} "
                f"| [{path.name}]({path.name}) |"
            )
    summary = out / SUMMARY
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip()[:7]


def _shown(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def print_report(result: Mapping[str, Any]) -> None:
    print(
        f"Data accuracy: {result['n']} reference dishes, dataset {result['dataset_version']}, "
        f"commit {result['commit']}"
    )
    print()
    print(f"{'nutrient':<20}{'gate':<10}{'n':>3}{'MAPE %':>9}  worst (APE %)")
    for key in EVALUATED:
        scored = result["per_nutrient"][key]
        worst = ", ".join(f"{entry['dish_id']} {entry['ape']}" for entry in scored["worst"])
        gate = "gated" if key in GATED else "reported"
        print(f"{key:<20}{gate:<10}{scored['n']:>3}{_cell(scored['mape']):>9}  {worst}")
    print()
    print("Per 100 g, reference / computed. * marks a computed value flagged incomplete.")
    header = ("dish", "reference", *EVALUATED, "ingredient sources")
    table = [header]
    for row in result["rows"]:
        cells = [
            _number(row["reference"][key])
            + " / "
            + _number(row["computed"][key])
            + ("*" if key in row["incomplete"] else "")
            for key in EVALUATED
        ]
        sources = ", ".join(row["ingredient_source_ids"])
        table.append((row["dish_id"], f"{row['source_id']} {row['record']}", *cells, sources))
    widths = [max(len(line[i]) for line in table) for i in range(len(header))]
    for line in table:
        print(
            "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)).rstrip()
        )


def _print_table(rows: Sequence[Sequence[str]]) -> None:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print(
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        )


def print_grounding_report(result: Mapping[str, Any]) -> None:
    print(
        f"Grounding: {result['n']} questions, model {result['model']}, "
        f"dataset {result['dataset_version']}, commit {result['commit']}"
    )
    print()
    _print_table(
        [
            ("measure", "without tools", "with tools", "n"),
            *(
                (
                    row["measure"],
                    _cell(row["without_tools"]),
                    _cell(row["with_tools"]),
                    str(row["n"]),
                )
                for row in result["summary"]
            ),
        ]
    )
    errors = result["tool_errors"]
    by_tool = ", ".join(f"{name} {count}" for name, count in errors["by_tool"].items())
    tokens = result["tokens"]
    print()
    print(
        f"Tool calls {result['tool_calls']}, tool errors {errors['count']}"
        f"{f' ({by_tool})' if by_tool else ''}, questions stopped at the turn cap "
        f"{result['turns_capped']}"
    )
    print(
        f"Tokens in/out: without tools {tokens['without_tools']['input']}/"
        f"{tokens['without_tools']['output']}, with tools {tokens['with_tools']['input']}/"
        f"{tokens['with_tools']['output']}"
    )


def run_grounding(limit: int | None, out: Path) -> int:
    """Run the grounding suite, write its results file and rebuild summary.md.

    The key is checked first, so a run without one starts no server and builds no client.
    """
    api_key = os.environ.get(API_KEY_VAR)
    if not api_key:
        print(
            f"error: {API_KEY_VAR} is not set. Put it in .env (see .env.example), which "
            "`make eval` loads, or set it in the environment.",
            file=sys.stderr,
        )
        return 1
    model = os.environ.get(MODEL_VAR) or grounding.DEFAULT_MODEL
    try:
        questions = grounding.load_questions(grounding.QUESTIONS)[:limit]
        result = anyio.run(
            functools.partial(
                grounding.run,
                grounding.gemini_client(model, api_key),
                questions,
                now=datetime.now(UTC),
                commit=git_commit(),
                dataset_version=dataset_version(loader.bundled_dir()),
            )
        )
    except (grounding.GroundingError, loader.DataError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    log.info("grounding asked %d questions of %s", result["n"], result["model"])
    print_grounding_report(result)
    try:
        path = write_results(result, out)
        summary = write_summary(out)
    except EvalError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(f"\nWrote {_shown(path)} and {_shown(summary)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging("INFO")
    parser = argparse.ArgumentParser(description="Run an evaluation suite.")
    parser.add_argument("--suite", required=True, choices=SUITES)
    parser.add_argument("--limit", type=int, metavar="N", help="grounding: first N questions")
    parser.add_argument(
        "--out", type=Path, default=RESULTS, help="folder for results files and summary.md"
    )
    parser.add_argument(
        "--check",
        type=Path,
        metavar="THRESHOLDS",
        help="compare with this thresholds file, write nothing, exit 1 on a failure",
    )
    args = parser.parse_args(argv)

    if args.suite == "grounding":
        if args.check is not None:
            parser.error("--check applies to the data_accuracy suite only")
        if args.limit is not None and args.limit < 1:
            parser.error("--limit must be 1 or more")
        return run_grounding(args.limit, args.out)
    if args.limit is not None:
        parser.error("--limit applies to the grounding suite only")

    try:
        result = data_accuracy(REFERENCES, now=datetime.now(UTC), commit=git_commit())
        thresholds = load_thresholds(args.check) if args.check is not None else None
    except (EvalError, loader.DataError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    log.info(
        "data_accuracy evaluated %d references against dataset %s",
        result["n"],
        result["dataset_version"],
    )
    print_report(result)

    if thresholds is not None:
        failures = check_thresholds(result["per_nutrient"], thresholds)
        print()
        for failure in failures:
            print(f"FAIL {failure}")
        print(f"Check against {_shown(args.check)}: {'failed' if failures else 'passed'}")
        return 1 if failures else 0

    try:
        path = write_results(result, args.out)
        summary = write_summary(args.out)
    except EvalError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(f"\nWrote {_shown(path)} and {_shown(summary)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
