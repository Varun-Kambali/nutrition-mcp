# 0002. USDA FoodData Central first, CoFID 2021 for the gaps
Date: 2026-10-19 · Status: accepted

## Context
Every ingredient value the server returns has to come from a source whose license allows redistribution ([ADR 0001](0001-own-recipes-over-copied-tables.md)). The standard Indian source, ICMR-NIN's Indian Food Composition Tables 2017 (IFCT), is a copyrighted book. The 2024 Indian Nutrient Databank is built on it and its repository carries no open license.

Two open sources cover raw ingredients. USDA FoodData Central is public domain and large, but it has no paneer, curry leaves or methi leaves, it has pigeon peas, mung beans and black gram only as whole seeds, and many of its flour, rice and bread records describe products enriched with iron and folic acid. CoFID 2021, the UK's composition dataset, is released under the Open Government Licence v3.0 and has some of what USDA lacks, including paneer, methi leaves, curry leaves, the split dals and rohu. Many of its Indian entries are older values taken from literature, with more gaps than USDA's records.

The two sources define some nutrients differently. USDA gives carbohydrate by difference, which includes fibre; CoFID gives available carbohydrate. USDA gives vitamin A as retinol activity equivalents; CoFID uses retinol equivalents, which give carotenes twice the vitamin A value that RAE does. Energy differs too: USDA SR Legacy counts fibre as carbohydrate when it computes kcal, at about 4 kcal/g for pulses, while CoFID gives fibre no energy.

## Decision
For each ingredient, pick the record in this order:

1. The record must describe the food in the form it is cooked: raw, the part that is eaten, and split dal or whole seed as the recipe uses it. Form beats source order.
2. USDA SR Legacy, because its records carry the full nutrient set. Foundation only when SR Legacy lacks the food.
3. CoFID 2021 when USDA lacks the food, has it only in the wrong form, or is a poor match (enriched bread, cream at a different fat level).
4. Leave the ingredient out when neither source has an acceptable record. Jaggery, ragi and poha are out for now.

Both sources are mapped onto one nutrient set by `nutrition_mcp.source_mapping`, with the rules written up in [data.md](../data.md): USDA carbohydrate minus fibre gives available carbohydrate, CoFID "Tr" is 0 and "N" is missing. Each row of `ingredients.csv` records its source and record ID, `sources.yaml` holds the license and attribution text for each source, and the bundled `LICENSE.md` explains both. `scripts/build_ingredients.py` builds the table from `data_build/ingredient_map.csv`, and every match is checked by hand against the source's own description before it is committed.

## Alternatives considered
- **Ship IFCT values.** Not allowed, because the book is copyrighted.
- **Use the Indian Nutrient Databank.** It derives from IFCT and has no open license.
- **Take values from recipe blogs or nutrition apps.** They have no traceable source and nothing to check them against.
- **USDA only.** Paneer, methi leaves and curry leaves would be missing, and the dals would be whole seeds with their coats, which carry more fibre than the split dal in the pot.
- **CoFID first.** UK white flour is fortified with calcium and iron, and many of CoFID's Indian entries lack fibre, folate or zinc values. USDA SR Legacy records are more complete for the common foods.
- **USDA Foundation first.** Its analyses are newer, but many records lack nutrients the server reports. The Foundation raw potatoes without skin have no fibre value, for example, so available carbohydrate could not be computed for them.
- **Convert CoFID vitamin A to retinol activity equivalents** from its retinol and carotene columns. Not done: the values are stored as each source reports them, and data.md states the difference.

## Consequences
Every ingredient value can be checked against a public record by its FDC ID or CoFID food code, and the attribution the OGL asks for ships with the data. Swapping a record is a one-line change to the map followed by `make data`.

The table mixes two sets of definitions. CoFID carbohydrate is expressed as monosaccharide equivalents, and a CoFID vitamin A value for a plant food is about twice what an RAE figure would be. Energy is on two bases: the fibre USDA counts makes up 11% to 25% of the kcal of the USDA pulses and 12% of atta, so whole urad from USDA reads 341 kcal per 100 g next to split urad dal from CoFID at 298. Energy is not recomputed; values stay as each source publishes them. data.md lists these differences so a reader can judge a number.

Some values are missing, and they stay missing. The four split dals from CoFID have no fibre value, so every dal dish will report fibre as incomplete until a better source turns up. Pav from CoFID carries UK flour fortification, so its calcium is higher than in Indian pav.

Watch the data-accuracy evaluation. It has to report the energy basis of each comparison, because a dish built from USDA pulses or atta will read high on kcal against a CoFID reference even when the recipe is right. If a dish is far from its reference because of one ingredient, look at that ingredient's record before changing the recipe.
