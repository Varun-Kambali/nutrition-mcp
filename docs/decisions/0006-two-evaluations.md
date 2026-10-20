# 0006. Two evaluations: data accuracy in CI, grounding on demand
Date: 2026-10-20 · Status: accepted

## Context
The server makes two claims that need evidence. The first is that its dish values are close to what the dishes contain. The second is that a model answers nutrition questions more accurately with its tools than from memory.

A grounding evaluation tests the second claim. Its golden answers are computed by the server's own query functions from the bundled data, so a model that calls the tools and adds correctly scores well even when an ingredient row or a recipe is wrong. It cannot test the first claim. It also calls a model, which is slow, rate-limited and not deterministic, so it cannot gate every pull request.

Dish values are computed from ingredient rows and recipes written for this project ([ADR 0001](0001-own-recipes-over-copied-tables.md)), and no open source publishes those recipes. What can be checked is how far a computed dish lands from an independent record of the same dish as eaten.

## Decision
Keep two separate suites in `evals/run_evals.py`.

**Data accuracy** compares the values the server computes per 100 g of each dish with one independent record of that dish, for kcal, protein, available carbohydrate, iron and calcium. It makes no model calls, so CI runs it on every pull request and every push to main.

- A reference is a CoFID 2021 homemade or recipe dish, or a USDA FoodData Central survey food (FNDDS), that describes the same dish as eaten. SR Legacy and Foundation records describe ingredients and never count, and neither does a record the dish already uses as an ingredient row. `scripts/build_references.py` enforces both.
- A match needs the same dish with a similar preparation: the same main ingredients, cooked, and not a dry mix. A loose match is worse than none, so a dish without one is left out. `data_build/reference_map.csv` names each record and says what differs from the bundled recipe: another fat, more or less water, an enriched grain, a different pulse or cheese. Each dish has one reference, so each counts once in an average.
- `scripts/build_references.py` fetches and maps the records with the same code that builds the ingredient table and writes them to `evals/datasets/references.csv` with each record's own description. Every row is read against that description before it is committed.
- For each nutrient the suite reports n, the mean absolute percentage error (MAPE) of the computed value against the reference, and the three worst dishes. A row is left out for a nutrient when its reference value is missing or 0, or when its computed value is missing or flagged incomplete. A computed value with a missing ingredient value is a floor ([ADR 0003](0003-missing-is-not-zero.md)), and comparing it would blame the recipe for a gap in the source.
- kcal, protein and available carbohydrate are gated. Iron and calcium are reported only: enriched rice and farina in the USDA survey recipes, and fortified flour in UK records, change them for reasons that have nothing to do with the recipe.
- `evals/thresholds.toml` holds the gate. `max_mape` is the baseline run's MAPE plus 5 points, rounded up to one decimal, and `min_n` is the baseline run's n. `--check` fails when a gated nutrient's MAPE is above its limit or when fewer dishes than `min_n` are usable, because dropping references could otherwise make a worse result look better. A deliberate change to the data or the references reruns the suite, commits the new results file and resets the thresholds by the same rule in the same pull request.

**Grounding** asks a free-tier Gemini model a set of natural-language questions twice with the same prompt: once from memory, and once with the server's tools over stdio. Each question lists the food IDs, amounts and units behind its golden answer, which the server's query functions compute. The model ends with a JSON answer, so scoring needs no judge model. It runs on demand with `make eval`, and its results file records the date, model, commit and cost. Its numbers will come from that results file.

A normal run of either suite writes a dated results file under `evals/results/` and rebuilds `evals/results/summary.md` from the newest results file of each suite, grounding first, copying each number exactly.

### Energy basis
CoFID kcal gives fibre no energy, while USDA SR Legacy kcal counts fibre as carbohydrate ([data.md](../data.md), [ADR 0002](0002-data-sources.md)). Every referenced dish has at least one USDA ingredient, so against a CoFID reference part of a kcal gap can be the energy basis and not the recipe. Chapati is the plainest case: its only ingredient is USDA atta, its reference is CoFID's chapati made without fat, and the baseline run has 202.0 kcal for the reference against 223.13 computed. Neither side's energy is recomputed. Each row of a results file lists the sources of the dish's ingredients (`ingredient_source_ids`) next to the reference's source, so the basis of each side can be read off.

### Same-source rows
When the reference and every ingredient of a dish come from CoFID, the comparison still tests the recipe (amounts and cooked weight) but not the ingredient values. Such rows are marked `same_source_ingredients: true`. No row in the baseline run is marked, because every referenced dish has at least one USDA ingredient.

## Baseline
Baseline run: `evals/results/2026-10-20_data-accuracy_32aa03d.json`, dataset version 24143edbb56e.

| Nutrient | Gate | n | MAPE (%) |
|---|---|---|---|
| kcal | gated | 18 | 18.7 |
| protein_g | gated | 18 | 24.8 |
| carbs_available_g | gated | 18 | 16.7 |
| iron_mg | reported | 18 | 44.2 |
| calcium_mg | reported | 18 | 46.1 |

The largest errors have causes that the reference notes already name:

- **Water in the recipe.** For kcal, musur_dal (reference 173.0, computed 79.12) and bengali_khichuri (169.0 against 87.9) are the worst rows. Both CoFID dishes hold much more lentil or rice per 100 g than the bundled recipes, which are cooked thin and soft.
- **Other proportions or ingredients.** For protein, rava_upma (reference 1.98, computed 3.49) and palak_paneer (5.42 against 8.83) are the worst rows. The FNDDS upma has far more onion and tomato for its farina than the bundled recipe, and the FNDDS palak paneer uses cottage cheese in place of paneer.
- **Enrichment and substitutes.** palak_paneer and rava_upma lead the worst lists for iron and calcium, and steamed_rice is third for iron. The FNDDS upma uses enriched farina, the FNDDS rice is enriched, and the FNDDS palak paneer is made with cottage cheese in place of paneer.

### Dishes without a reference
- **No record of the dish in CoFID 2021 or FNDDS:** aloo_posto, bisi_bele_bath, coconut_chutney, curd_rice, dhokla, lemon_rice, methi_thepla, misal_pav, pav_bhaji, rasam, sabudana_khichdi, shukto, varan_bhaat and zunka.
- **Only a related dish:** machher_jhol (CoFID's Bangladeshi fish curry has no potato, which supplies most of this dish's available carbohydrate, so the carbohydrate comparison would measure the potato), idli (the FNDDS idli mixes cooked glutinous rice with more split peas than rice), batata_vada (CoFID's potato pakora is sliced potato in batter, and the FNDDS vada is a lentil fritter), begun_bhaja (CoFID's fried aubergine is a cooked-vegetable record, not a dish, and the FNDDS fried eggplant is breaded), punjabi_kadhi (CoFID's khadhi has no pakoras), ghugni (CoFID's pea dishes use green or canned peas, not dried), dalma (CoFID's mixed dahl has no vegetables), goan_fish_curry (no coconut fish curry; the FNDDS fish curry is a generic sauce with haddock and shrimp) and ven_pongal (close to CoFID's khichadi, but a different dish, so it gets no reference, as with GI values in [data.md](../data.md)).

## Alternatives considered
- **Grounding only.** It would show that the model uses the tools, not that the data is right.
- **References from IFCT 2017 or the Indian Nutrient Databank.** Neither is openly licensed ([ADR 0002](0002-data-sources.md)), so their values cannot be committed.
- **Several references per dish, averaged.** A dish with more records would weigh more, and the choice of record would be hidden in an average. One reference per dish keeps each choice visible in the map.
- **Gate iron and calcium too.** Enrichment and fortification in the references cause some of their largest errors, so a gate would partly track US and UK food policy rather than the recipes.
- **A fixed threshold chosen in advance.** There was no basis for a number before the first run. The gate instead catches changes that make the data worse than the measured baseline.
- **Recompute energy on one basis.** Values stay as each source publishes them (ADR 0002); the results show each side's sources instead.
- **Mean absolute error in grams and kcal.** It is not comparable across nutrients. MAPE is. Its weakness is that a small reference value turns a small gap into a large percentage, which the worst lists make visible.

## Consequences
A pull request that moves dish values away from the references, through a recipe, an ingredient row or the loader, fails CI and names the nutrient. The check needs no API key and no model.

The baseline measures distance from one independent record per dish, not distance from the truth. The references are UK and US records, so a gap can be a regional difference (a thicker dal, another fat) rather than an error, and n is small enough that one dish moves an average. Fixing a gap means checking the recipe against the reference notes first, then the ingredient records.

Watch the worst lists when the data changes. If a recipe change lowers one dish's error and the gate still fails, look at which dishes lost or gained a usable value before resetting the thresholds.
