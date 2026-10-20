# Evaluation results

Rebuilt by `evals/run_evals.py` from the newest results file of each suite. Do not
edit by hand. Each number is copied from the results file named on its row.

Data accuracy compares the values computed for each bundled dish with an independent
record of the same dish (`evals/datasets/references.csv`). n is the number of dishes
used for a nutrient, and the value is the mean absolute percentage error against the
reference. A gated nutrient fails CI when its MAPE is above, or its n below, the limits
in `evals/thresholds.toml`.

| Suite | Measure | n | Value | Date | Commit | Dataset version | Results file |
|---|---|---|---|---|---|---|---|
| data accuracy | Energy (kcal) MAPE (%), gated | 19 | 18.9 | 2026-10-20 | 41edc33 | 24143edbb56e | [2026-10-20_data-accuracy_41edc33.json](2026-10-20_data-accuracy_41edc33.json) |
| data accuracy | Protein (g) MAPE (%), gated | 19 | 25.0 | 2026-10-20 | 41edc33 | 24143edbb56e | [2026-10-20_data-accuracy_41edc33.json](2026-10-20_data-accuracy_41edc33.json) |
| data accuracy | Available carbohydrate (g) MAPE (%), gated | 19 | 27.2 | 2026-10-20 | 41edc33 | 24143edbb56e | [2026-10-20_data-accuracy_41edc33.json](2026-10-20_data-accuracy_41edc33.json) |
| data accuracy | Iron (mg) MAPE (%), reported | 19 | 42.2 | 2026-10-20 | 41edc33 | 24143edbb56e | [2026-10-20_data-accuracy_41edc33.json](2026-10-20_data-accuracy_41edc33.json) |
| data accuracy | Calcium (mg) MAPE (%), reported | 18 | 46.1 | 2026-10-20 | 41edc33 | 24143edbb56e | [2026-10-20_data-accuracy_41edc33.json](2026-10-20_data-accuracy_41edc33.json) |
