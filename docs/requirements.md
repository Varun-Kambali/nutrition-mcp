# Requirements

Status: draft, 2026-10-19. Boxes are ticked as each feature ships. The reasoning behind the project is in [case-study.md](case-study.md); the design is in [architecture.md](architecture.md).

## Problem

AI assistants answer nutrition questions about Indian food from memory. The numbers have no source and no stated portion, and the totals are often added up wrong. That matters most for people managing diabetes or a nutrient deficiency, which are common health problems in India. The authoritative Indian tables can't be redistributed, and the largest open database covers few Indian dishes.

## Users

1. A person asking an MCP-capable assistant about food they eat or plan to cook.
2. A person managing diabetes or a diagnosed deficiency, through an assistant or a planner agent.
3. A developer adding nutrition data to an assistant or agent. The first one is the meal-planner agent I build next, which will call this server for every number.
4. A team that wants to run the server on its own data (a licensed food table, lab-tested recipes, a menu).

## Definition of done

v1 is done when a person could rely on it: every MVP box below is ticked, it installs and runs from a fresh clone by following the README, the tests and evaluations pass in CI, and nothing breaks on the basic paths (bad input, missing foods, unknown units). Progress is tracked against the milestones, not dates.

## MVP features

### Nutrients

Every food carries the same nutrient set, per 100 g, each in one declared unit:

| Group | Nutrients |
|---|---|
| Energy and macronutrients | kcal, protein (g), fat (g), saturated fat (g), available carbohydrate (g), total sugars (g), fibre (g) |
| Minerals | sodium (mg), iron (mg), calcium (mg), zinc (mg) |
| Vitamins | folate (µg), vitamin A (µg), vitamin B12 (µg), vitamin C (mg), vitamin D (µg) |

- [ ] **Nutrient definitions.** `docs/data.md` lists each nutrient's unit and how each source's value maps to it. USDA reports carbohydrate by difference (fibre included) and CoFID reports available carbohydrate; the server stores available carbohydrate, so USDA values have fibre subtracted. Vitamin A, folate and fibre methods differ between the sources in similar ways, and each mapping is written down.
- [ ] **Unknown is not zero.** A nutrient a source doesn't report is stored as missing. A dish whose ingredients have a missing value reports that nutrient as incomplete and names the ingredients, never a silent under-count.

### Data

- [ ] **Ingredients.** About 60 raw ingredients common in Indian cooking, from USDA FoodData Central (public domain) first and CoFID 2021 (Open Government Licence v3.0) where USDA lacks the ingredient or only has a poor match. Each row has its source, the source record ID and the license, plus Indian and English names and common spelling variants. Every source match is reviewed by hand, because search results are sometimes wrong (USDA's top hit for "bottle gourd" is towel gourd).
- [ ] **Gaps.** An ingredient missing from both sources (jaggery, ragi and poha at kickoff) is left out until an openly licensed source is found. A dish that needs it as a core ingredient is dropped or replaced with another dish. A dish that uses only a small, optional amount may leave it out, and the dish's assumptions say so. Never invent a value.
- [ ] **Dishes.** About 40 dishes, 10 per region (`north`, `south`, `east`, `west`; the Northeast is tagged `east` for now). Pan-India dishes carry all four tags. Each dish lists raw ingredient grams, cooked weight, servings, a diet tag (`veg`, `egg`, `non_veg`, `vegan`), region tags, household measures in grams, and an assumptions note that states at least the oil or ghee per serving. Each recipe is reviewed by hand before it ships.
- [ ] **Computed dish nutrition.** At load time, a dish's per-100 g values are the sum of its ingredients' nutrients divided by its cooked weight. No dish value is entered by hand. Cooking losses are not applied in v1, and the output says so for micronutrients.
- [ ] **Glycemic index and load.** A dish or ingredient gets a GI value only when the CC BY 4.0 compendium of GI values for non-Western foods (Henry et al., 2021) lists the same food with a similar preparation. The match records the compendium's food name and table. Glycemic load per serving is computed as GI × available carbohydrate per serving ÷ 100. With no match, GI and GL are missing, never estimated.
- [ ] **Household measures.** katori, piece, roti, tbsp, tsp, cup and glass, with grams per food. A default katori of 150 g is defined once in config and can be overridden per dish.
- [ ] **Sources resource.** One MCP resource lists every loaded dataset: name, version, URL, license, retrieval date and the attribution text the license asks for.
- [ ] **Bring your own data.** A documented folder format (`sources.yaml`, `ingredients.csv`, `dishes/*.yaml`) with a JSON Schema for each file. The server loads any extra folders named in `NUTRITION_MCP_DATA_DIRS`. The bundled data goes through the same loader.
- [ ] **Validation.** A bad row stops startup with a message naming the file, the row and the problem. A dish that uses an unknown ingredient, or an ID that appears in two datasets, also stops startup.

### Tools

All tools are read-only, return structured output from Pydantic models, and are marked read-only and idempotent in their MCP annotations.

- [ ] **`search_food(query, region?, diet?, kind?, limit?)`**: fuzzy match on names and spelling variants ("sambar" and "sambhar" both work). Returns ID, names, kind (dish or ingredient), regions, diet and source. With no good match, it returns an empty result and up to 5 close suggestions.
- [ ] **`get_nutrition(food_id, amount, unit)`**: the full nutrient set for that amount, plus glycemic load when GI is known. `unit` is `g`, `ml` or a household measure defined for the food. The output states the grams used, the measure assumption, the source, any incomplete nutrients and, for dishes, the oil assumption. An unknown unit returns an error that lists the valid units for that food.
- [ ] **`compute_meal(items)`**: per-item and total nutrients and glycemic load for a list of `{food_id, amount, unit}`. If any item fails, the error names it. No item is dropped quietly. The meal's glycemic load is the sum of the items' loads, and it is marked incomplete if any item's GI is missing.
- [ ] **`find_foods(rank_by, per, region?, diet?, kind?, limit?)`**: ranks foods by any nutrient, `per` 100 kcal, 100 g or serving (serving applies to dishes only) (highest first, or lowest first for kcal, sugars, saturated fat, sodium and glycemic load). Foods missing the ranked value are left out and counted in the response.
- [ ] **`compute_targets(sex, age, height_cm, weight_kg, activity, goal)`**: daily calorie and protein targets for adults (18 and over). It uses the Mifflin-St Jeor equation for resting energy, an activity multiplier, and a goal adjustment for `lose`, `maintain` or `gain`. Every constant cites its source in the code and in an ADR. The output names the formula, echoes the inputs, applies a calorie floor, and says it is an estimate, not medical advice. Nothing is stored.

### Transport and packaging

- [ ] stdio transport, working in Claude Desktop and Claude Code, with copy-paste config in the README.
- [ ] Runs from a clone with `uv`, and straight from GitHub with `uvx --from git+https://github.com/Varun-Kambali/nutrition-mcp nutrition-mcp`. No PyPI release is needed for v1.
- [ ] Logs are structured JSON on stderr, because stdout carries the protocol.

### Quality

- [ ] Unit tests for loading, validation, nutrient mapping, unit conversion, dish computation and glycemic load. In-memory MCP client tests for every tool, including the error paths.
- [ ] At least 80% coverage on `src/`.
- [ ] CI runs lint, type check, tests, the data-accuracy evaluation, gitleaks and the Docker build.
- [ ] Every tool checked by hand in MCP Inspector and in Claude Code.

### Evaluation

- [ ] **Data accuracy.** For every bundled dish that also appears in an independent source (CoFID's homemade dishes such as sambar, saag, raita and the dal curries; USDA's survey foods such as dal and idli), compare the computed values per 100 g with the reference. kcal, protein and available carbohydrate are the main measures; iron and calcium are reported too. Report n, mean absolute percentage error and the worst cases. No model calls, so it runs in CI, with thresholds in `evals/thresholds.toml` set after the first run.
- [ ] **Grounding lift.** About 30 natural-language questions ("protein in 2 rotis and a katori of rajma?", "carbs in a plate of idli and sambar?"). Each question's golden answer is computed by the server's own functions from listed food IDs, amounts and units. The same model answers each question without tools and with the server's tools. Report mean absolute percentage error on kcal, protein and carbohydrate, the share of answers within 10%, and tool-call errors. The model is a free-tier Gemini model. This runs on demand (`make eval`), and results are committed with the date, model ID, commit and cost.
- [ ] **Latency.** p95 tool latency across the grounding run.

### Docs

- [ ] README per the template: demo GIF, results table, Mermaid diagram, quickstart, limitations, and data licenses with attribution.
- [ ] `docs/data.md` with the nutrient definitions and source mappings.
- [ ] The case study, kept current. An ADR for each decision.
- [ ] `portfolio.json` and the `portfolio` repo topic.

## Non-goals for v1

- Health profiles, conditions, diagnosed deficiencies, reference intakes and condition-aware planning. These belong to the planner agent.
- Meal planning, recommendations or remembering a user.
- Cooking-loss (retention) factors for vitamins.
- Packaged foods and barcode lookup (Open Food Facts).
- Regional prices and availability (Agmarknet).
- Remote hosting, authentication and rate limits.
- A PyPI release or an MCP Registry listing.
- Targets for anyone under 18.

## Anti-goals: v1 fails if any of these happen, even if it runs

- A number goes out with no source or no stated portion.
- A missing nutrient value is reported as zero.
- A dish value is typed in by hand, or copied from a source we can't redistribute.
- A GI value is guessed for a food the compendium doesn't list.
- Model arithmetic is presented as tool output.
- The server infers a condition or deficiency, or suggests a medicine or a dose.
- The README claims a result that isn't in a committed results file.
- A missing food or unit is quietly treated as something else.

## Constraints

- **Progress, not dates.** Milestones finish when their checks pass, not on a date.
- **Cost:** free only. A free USDA API key for the build script, and the Gemini free tier for the grounding evaluation. Both keys live in `.env`, never in the repo.
- **Licenses:** only public-domain or openly licensed data, with the source and license on every row. USDA data is public domain; CoFID needs the Open Government Licence attribution; the GI compendium needs CC BY 4.0 attribution. Code is MIT. The authored recipes are CC BY 4.0.
- **Public from the first commit,** with no personal data.
- **Stack:** Python 3.12, uv, MCP Python SDK 2.x (2.3.0 at kickoff), which speaks the 2026-07-28 protocol and still serves older clients.

## Success metrics

1. Headline: calorie error without tools vs with tools on the grounding set.
2. Computed dish values against independent references.
3. p95 tool latency.
4. Every MVP box ticked from a fresh clone.

## Open items

- Openly licensed sources for jaggery, ragi and poha.
- How many bundled dishes have an independent reference. This sets n for the data-accuracy evaluation.
