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
| data accuracy | Energy (kcal) MAPE (%), gated | 18 | 18.7 | 2026-10-20 | 32aa03d | 24143edbb56e | [2026-10-20_data-accuracy_32aa03d.json](2026-10-20_data-accuracy_32aa03d.json) |
| data accuracy | Protein (g) MAPE (%), gated | 18 | 24.8 | 2026-10-20 | 32aa03d | 24143edbb56e | [2026-10-20_data-accuracy_32aa03d.json](2026-10-20_data-accuracy_32aa03d.json) |
| data accuracy | Available carbohydrate (g) MAPE (%), gated | 18 | 16.7 | 2026-10-20 | 32aa03d | 24143edbb56e | [2026-10-20_data-accuracy_32aa03d.json](2026-10-20_data-accuracy_32aa03d.json) |
| data accuracy | Iron (mg) MAPE (%), reported | 18 | 44.2 | 2026-10-20 | 32aa03d | 24143edbb56e | [2026-10-20_data-accuracy_32aa03d.json](2026-10-20_data-accuracy_32aa03d.json) |
| data accuracy | Calcium (mg) MAPE (%), reported | 18 | 46.1 | 2026-10-20 | 32aa03d | 24143edbb56e | [2026-10-20_data-accuracy_32aa03d.json](2026-10-20_data-accuracy_32aa03d.json) |
