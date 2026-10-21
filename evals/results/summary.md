# Evaluation results

Rebuilt by `evals/run_evals.py` from the newest results file of each suite. Do not
edit by hand. Each number is copied from the results file named on its row.

Grounding asks a model each question in `evals/datasets/questions.jsonl` twice with the
same prompt. Baseline is the model answering from memory; This project is the same
model calling this server's tools over stdio. Golden answers are computed by the
server's own query functions from the bundled data. An error is the mean absolute
percentage error over the questions both columns answered with numbers, so the two
means cover the same questions. The share within 10% counts every question with items
in each column, and an answer without numbers counts as outside 10%, as does a request
the API refused. A question whose request was still rate-limited or unreachable after
its retries is an API error: it is counted on its own row and left out of every other
one. Missing dishes, tool errors and latency have no Baseline value: without tools the
model has no data to look a dish up in and calls no tools.
Shares run from 0 to 1. n is the number of questions behind each value, Baseline first,
or of tool calls for tool errors and latency. The model runs at the provider's default
temperature, so answers vary from run to run.

| Measure | Baseline | This project | n | Model | Date | Commit | Dataset version | Results file |
|---|---|---|---|---|---|---|---|---|
| Calorie error against the dataset totals, MAPE (%) | 16.0 | 0.0 | 26 / 26 | gemini-3.1-flash-lite | 2026-10-21 | 8262476 | 24143edbb56e | [2026-10-21_gemini-3.1-flash-lite_8262476.json](2026-10-21_gemini-3.1-flash-lite_8262476.json) |
| Protein error against the dataset totals, MAPE (%) | 13.2 | 0.0 | 26 / 26 | gemini-3.1-flash-lite | 2026-10-21 | 8262476 | 24143edbb56e | [2026-10-21_gemini-3.1-flash-lite_8262476.json](2026-10-21_gemini-3.1-flash-lite_8262476.json) |
| Carbohydrate error against the dataset totals, MAPE (%) | 20.2 | 0.0 | 26 / 26 | gemini-3.1-flash-lite | 2026-10-21 | 8262476 | 24143edbb56e | [2026-10-21_gemini-3.1-flash-lite_8262476.json](2026-10-21_gemini-3.1-flash-lite_8262476.json) |
| Answers within 10% on calories (share) | 0.38 | 1.0 | 26 / 26 | gemini-3.1-flash-lite | 2026-10-21 | 8262476 | 24143edbb56e | [2026-10-21_gemini-3.1-flash-lite_8262476.json](2026-10-21_gemini-3.1-flash-lite_8262476.json) |
| Missing dishes reported (share) | n/a | 0.5 | n/a / 4 | gemini-3.1-flash-lite | 2026-10-21 | 8262476 | 24143edbb56e | [2026-10-21_gemini-3.1-flash-lite_8262476.json](2026-10-21_gemini-3.1-flash-lite_8262476.json) |
| Failed answers (count) | 0 | 1 | 30 / 30 | gemini-3.1-flash-lite | 2026-10-21 | 8262476 | 24143edbb56e | [2026-10-21_gemini-3.1-flash-lite_8262476.json](2026-10-21_gemini-3.1-flash-lite_8262476.json) |
| API errors (count) | 0 | 0 | 30 / 30 | gemini-3.1-flash-lite | 2026-10-21 | 8262476 | 24143edbb56e | [2026-10-21_gemini-3.1-flash-lite_8262476.json](2026-10-21_gemini-3.1-flash-lite_8262476.json) |
| Tool errors (count) | n/a | 0 | n/a / 89 | gemini-3.1-flash-lite | 2026-10-21 | 8262476 | 24143edbb56e | [2026-10-21_gemini-3.1-flash-lite_8262476.json](2026-10-21_gemini-3.1-flash-lite_8262476.json) |
| p95 tool latency (ms) | n/a | 10.4 | n/a / 89 | gemini-3.1-flash-lite | 2026-10-21 | 8262476 | 24143edbb56e | [2026-10-21_gemini-3.1-flash-lite_8262476.json](2026-10-21_gemini-3.1-flash-lite_8262476.json) |

Data accuracy compares the values computed for each bundled dish with an independent
record of the same dish (`evals/datasets/references.csv`). n is the number of dishes
used for a nutrient, and the value is the mean absolute percentage error against the
reference. A gated nutrient fails CI when its MAPE is above, or its n below, the limits
in `evals/thresholds.toml`.

| Suite | Measure | n | Value | Date | Commit | Dataset version | Results file |
|---|---|---|---|---|---|---|---|
| data accuracy | Energy (kcal) MAPE (%), gated | 18 | 18.7 | 2026-10-20 | 32aa03d | 24143edbb56e | [2026-10-20_data-accuracy_32aa03d.json](2026-10-20_data-accuracy_32aa03d.json) |
| data accuracy | Protein (g) MAPE (%), gated | 18 | 24.8 | 2026-10-20 | 32aa03d | 24143edbb56e | [2026-10-20_data-accuracy_32aa03d.json](2026-10-20_data-accuracy_32aa03d.json) |
| data accuracy | Available carbohydrate (g) MAPE (%), gated | 18 | 16.7 | 2026-10-20 | 32aa03d | 24143edbb56e | [2026-10-20_data-accuracy_32aa03d.json](2026-10-20_data-accuracy_32aa03d.json) |
| data accuracy | Iron (mg) MAPE (%), reported | 18 | 44.2 | 2026-10-20 | 32aa03d | 24143edbb56e | [2026-10-20_data-accuracy_32aa03d.json](2026-10-20_data-accuracy_32aa03d.json) |
| data accuracy | Calcium (mg) MAPE (%), reported | 18 | 46.1 | 2026-10-20 | 32aa03d | 24143edbb56e | [2026-10-20_data-accuracy_32aa03d.json](2026-10-20_data-accuracy_32aa03d.json) |
