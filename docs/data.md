# Data notes

How the bundled ingredient table is built, how each source's values map onto the server's nutrients, and where the two sources define a nutrient differently. The licenses and attribution text for every data file are in [`src/nutrition_mcp/data/bundled/LICENSE.md`](../src/nutrition_mcp/data/bundled/LICENSE.md). Why these two sources were chosen is in [ADR 0002](decisions/0002-data-sources.md).

## Building the ingredient table

`data_build/ingredient_map.csv` names one source record per ingredient:

| Column | Holds |
|---|---|
| `id` | Lowercase slug used by recipes and tools |
| `name` | The Indian name, with the English name in parentheses where they differ |
| `aliases` | Other spellings and names, separated by `\|` |
| `diet` | `vegan` for plant foods, `veg` for dairy and ghee, `egg`, `non_veg` for fish |
| `source` | `usda` or `cofid` (written to `ingredients.csv` as `usda_fdc` or `cofid_2021`) |
| `record` | The USDA FDC ID or the CoFID food code |
| `notes` | Why this record, when the choice is not obvious. Not copied to `ingredients.csv` |

`make data` runs `scripts/build_ingredients.py`, which:

1. Fetches each USDA record from `https://api.nal.usda.gov/fdc/v1/food/{fdcId}` and caches it in `.cache/usda/`. The API key is sent in the `X-Api-Key` header and is only needed for records that are not cached yet: `set -a; . ./.env; set +a; make data`.
2. Downloads the CoFID 2021 spreadsheet once into `.cache/` and checks its SHA-256, so a replaced file on gov.uk stops the build instead of changing values silently. The sheets "1.3 Proximates", "1.4 Inorganics" and "1.5 Vitamins" are joined on the food code.
3. Maps each record onto the server's nutrients with `nutrition_mcp.source_mapping`, validates the row with the same model the loader uses, and writes `src/nutrition_mcp/data/bundled/ingredients.csv`.
4. Prints a review table: our name, the source, the record and the source's own description, kcal, protein and the number of missing nutrients.

Every row of the review table is checked by hand against the source description: the right food, raw and not cooked, the part that is eaten, and split dal or whole seed. Search results are not trusted: USDA's top hit for "bottle gourd" is towel gourd, and the right record is "Gourd, white-flowered (calabash), raw". `tests/integration/test_bundled_data.py` checks that each row's id, name, aliases, diet, source and record in `ingredients.csv` match the map, so a map change without a rebuild, or a hand edit to those columns, fails the tests. It cannot check nutrient values, because CI has no copy of the source records. To check those, run `make data` (it works offline from `.cache/`) and confirm that `git diff` shows no change to `ingredients.csv`; the build writes the same bytes from the same records.

## Choosing a record

1. **Form first.** The record has to describe the food as it goes into the pot: raw, flesh only for potato, pods and not leaves for drumstick, split and skinned for a dal. When USDA has only the whole seed and CoFID has the split dal, CoFID wins.
2. **USDA SR Legacy next.** Its records carry the full nutrient set with an energy value in kcal. Foundation records are newer but often lack nutrients this server reports. The Foundation raw potatoes without skin, for example, have no fibre value, so available carbohydrate could not be computed. Every USDA row in the current table is SR Legacy.
3. **CoFID 2021 where USDA lacks the food or only has a poor match.**
4. **Unenriched records where USDA has both.** Most US flour, rice and bread is enriched with iron and folic acid, and those additions should not be counted for an Indian kitchen.

Twelve of the 66 ingredients come from CoFID; the other 54 are USDA SR Legacy:

| Ingredient | CoFID record | Why not USDA |
|---|---|---|
| `pav` | 11-1006 Bread rolls, white, soft | USDA white breads and rolls are enriched with iron and folic acid. UK white flour is fortified with calcium and iron, so calcium here is higher than in Indian pav |
| `toor_dal` | 13-104 Beans, pigeon peas, dahl, dried, raw | USDA has only the whole seed |
| `moong_dal` | 13-098 Beans, mung, dahl, dried, raw | USDA has only the whole bean |
| `urad_dal` | 13-060 Black gram, duhli urad dahl, split, dried, raw | USDA has only the whole bean |
| `chana_dal` | 13-076 Beans, chick peas, Kabuli, split, dried, raw | USDA has only the whole seed. Chana dal is split desi chickpea, which neither source has |
| `cream` | 12-337 Cream, dairy, extra thick (24% fat) | Indian fresh cream is about 25% fat; USDA's nearest creams are 19% and 31% |
| `paneer` | 12-495 Cheese, Paneer | Not in USDA |
| `potato` | 13-489 Potatoes, old, raw, flesh only | SR Legacy has raw potato only with the skin; Foundation has no fibre value |
| `methi_leaves` | 13-243 Fenugreek leaves, raw | USDA has fenugreek seed only |
| `curry_leaves` | 13-821 Curry leaves, fresh | Not in USDA |
| `garam_masala` | 13-829 Garam masala | Not in USDA |
| `rohu` | 16-132 Rohu, raw | Not in USDA. USDA's common carp has 5.6 g fat per 100 g against rohu's 1.4 g, so it is not a fair stand-in. The record's vitamin A and vitamin D cells are "Tr" (trace) and are stored as 0 |

### Left out

Jaggery, ragi (finger millet) and poha (flattened rice) have no record in USDA SR Legacy or Foundation or in CoFID 2021 (searched 2026-10-19). USDA's millet records do not name finger millet, and a USDA search for "poha" finds the cape gooseberry, which is also called poha. These stay out until an openly licensed source is found, and dishes that need them are dropped or replaced.

## Nutrients

All values are per 100 g of the food as described by the source record.

| Column | Unit | USDA nutrient ID | CoFID 2021 column |
|---|---|---|---|
| `kcal` | kcal | 1008 Energy; if absent, 2048 Energy (Atwater Specific Factors), then 2047 Energy (Atwater General Factors) | Energy (kcal) (kcal) |
| `protein_g` | g | 1003 Protein | Protein (g) |
| `fat_g` | g | 1004 Total lipid (fat) | Fat (g) |
| `sat_fat_g` | g | 1258 Fatty acids, total saturated | Satd FA /100g fd (g) |
| `carbs_available_g` | g | 1005 Carbohydrate, by difference, minus 1079 | Carbohydrate (g) |
| `sugars_g` | g | 2000 Total Sugars; if absent, 1063 (the ID Foundation records use) | Total sugars (g) |
| `fibre_g` | g | 1079 Fiber, total dietary | AOAC fibre (g) |
| `sodium_mg` | mg | 1093 Sodium, Na | Sodium (mg) |
| `iron_mg` | mg | 1089 Iron, Fe | Iron (mg) |
| `calcium_mg` | mg | 1087 Calcium, Ca | Calcium (mg) |
| `zinc_mg` | mg | 1095 Zinc, Zn | Zinc (mg) |
| `folate_ug` | µg | 1177 Folate, total | Folate (µg) |
| `vit_a_ug` | µg | 1106 Vitamin A, RAE | Retinol Equivalent (µg) |
| `vit_b12_ug` | µg | 1178 Vitamin B-12 | Vitamin B12 (µg) |
| `vit_c_mg` | mg | 1162 Vitamin C, total ascorbic acid | Vitamin C (mg) |
| `vit_d_ug` | µg | 1114 Vitamin D (D2 + D3) | Vitamin D (µg) |

USDA values are matched on `foodNutrients[].nutrient.id`. When a list has more than one ID, the first one the record reports is used. A USDA value in any unit other than the one above (vitamin D in IU, for example) stops the build rather than being stored 40 times too large. A missing CoFID column also stops the build.

## Where the sources define a nutrient differently

**Carbohydrate.** USDA reports carbohydrate by difference: what is left of 100 g after water, protein, fat and ash, which includes fibre. The server stores available carbohydrate, so a USDA value is carbohydrate by difference minus total dietary fibre (1079), floored at 0. If either value is missing, available carbohydrate is missing too (moth beans have no fibre value, so their available carbohydrate is empty). CoFID reports available carbohydrate directly, as the sum of starch and sugars (plus oligosaccharides where they were measured), expressed as monosaccharide equivalents. That convention makes CoFID figures slightly heavier for the same food: CoFID's demerara sugar has 104.5 g carbohydrate per 100 g. The same applies to CoFID sugars.

**Fibre.** Both columns are total dietary fibre measured by AOAC methods. CoFID also has an NSP (Englyst) column, which gives lower values because it leaves out lignin and resistant starch; the server does not use it. Older CoFID records taken from literature, including all four split dals, have no AOAC value, so their fibre is missing rather than filled from NSP or from the whole seed.

**Vitamin A.** USDA's 1106 is retinol activity equivalents (RAE), which count 12 µg of beta-carotene as 1 µg of retinol. CoFID's retinol equivalent counts 6 µg of beta-carotene as 1 µg of retinol. For a plant food, where all the vitamin A comes from carotenes, the CoFID figure is about twice the RAE figure for the same carotene content. This affects the CoFID rows for methi leaves and curry leaves most. The values are stored as each source reports them.

**Energy.** The two sources put different things into kcal, and the difference is largest for pulses and whole grains.

- USDA SR Legacy multiplies protein, fat and carbohydrate by difference by Atwater factors, specific to the food where USDA has them (each record lists its factors). Carbohydrate by difference includes fibre, so fibre counts for energy at the carbohydrate factor. For the whole pulses in this table the factors are 3.47 kcal/g for protein, 8.37 kcal/g for fat and 4.07 kcal/g for carbohydrate.
- CoFID computes kcal as 4 x protein + 9 x fat + 3.75 x available carbohydrate (in monosaccharide equivalents) + 7 x alcohol. Fibre counts for nothing. That formula reproduces the stated kcal of 2,617 of the 2,853 CoFID foods that have all the inputs, and of 8 of the 12 CoFID rows here. The other four (urad dal, methi leaves, curry leaves, garam masala) are literature values whose stated kcal does not follow it.

The size of the gap is roughly the energy USDA gives fibre. In this table that is 11% of the kcal of besan, 12% of atta and masoor dal, 13% of kabuli chana, 18% of rajma, 22% of whole urad and 25% of dried split peas. It is under 5% for rice, maida, rava and sabudana, and zero for oils, ghee, dairy, fish and eggs. Vegetables show large shares (up to 59% for bitter gourd) but carry few kcal per serving.

An example from this table: whole urad from USDA (174259) is 341 kcal per 100 g, of which 74.5 kcal is its 18.3 g of fibre at 4.07 kcal/g. Split urad dal from CoFID (13-060) is 298 kcal, with no energy from fibre. The two numbers are on different bases, so the 43 kcal between them says little about how the foods differ.

The server stores kcal as each source publishes it and does not recompute energy. The data-accuracy evaluation has to account for this: a dish built mostly from USDA pulses or atta, compared with a CoFID reference dish, will read high on kcal by roughly the fibre energy of its ingredients even when the recipe is right. Its results need to show the energy basis of each side of a comparison, so that this gap is not read as a recipe error.

**Folate.** USDA's 1177 is total folate in µg (food folate plus any folic acid), not dietary folate equivalents. CoFID's folate is also total folate in µg. The unenriched USDA records chosen here contain no folic acid.

**Trace and missing values in CoFID.** "Tr" (a trace) is stored as 0. "N" means the nutrient is present in significant amounts but CoFID has no reliable value; it is stored as missing, as are blank cells. A missing value is never stored as 0 ([ADR 0003](decisions/0003-missing-is-not-zero.md)).

## Density

`density_g_per_ml` is set only for ingredients poured or spooned as liquids (whole milk, oils, ghee, lemon juice, coconut milk), and only when the USDA record has a portion whose unit is a bare "cup" or "tbsp" (a "cup, chopped" portion describes a different state of the food). It is the portion's gram weight divided by its volume, taking 1 cup as 236.588 ml and 1 tbsp as 14.787 ml, and a cup portion is preferred over a tablespoon because its gram weight carries less rounding.

## Missing values in the bundled table

| Ingredient | Source | Missing |
|---|---|---|
| `rava` | usda_fdc | sugars_g |
| `toor_dal` | cofid_2021 | fibre_g |
| `moong_dal` | cofid_2021 | fibre_g |
| `urad_dal` | cofid_2021 | fibre_g |
| `chana_dal` | cofid_2021 | fibre_g |
| `masoor_dal` | usda_fdc | sugars_g |
| `whole_urad` | usda_fdc | sugars_g |
| `moth_beans` | usda_fdc | carbs_available_g, sugars_g, fibre_g |
| `paneer` | cofid_2021 | vit_b12_ug, vit_d_ug |
| `mustard_oil` | usda_fdc | sugars_g, vit_d_ug |
| `bitter_gourd` | usda_fdc | sat_fat_g, sugars_g |
| `drumstick` | usda_fdc | sugars_g |
| `bottle_gourd` | usda_fdc | sugars_g, vit_a_ug |
| `raw_banana` | usda_fdc | vit_a_ug, vit_b12_ug, vit_d_ug |
| `methi_leaves` | cofid_2021 | sat_fat_g, sugars_g, fibre_g, zinc_mg, folate_ug |
| `curry_leaves` | cofid_2021 | sat_fat_g, sugars_g, fibre_g, sodium_mg, zinc_mg |
| `garam_masala` | cofid_2021 | sat_fat_g, sugars_g, fibre_g |
| `rohu` | cofid_2021 | sat_fat_g, fibre_g, calcium_mg, zinc_mg, folate_ug |
| `surmai` | usda_fdc | sugars_g, vit_d_ug |

A dish that uses one of these ingredients reports the nutrient as incomplete and names the ingredient.

Other values to read with care, all kept as published and noted in the map:

- `rohu`: vitamin A and vitamin D are "Tr" in CoFID and stored as 0.
- `dried_peas`: folate is 15 µg per 100 g, far below what USDA's boiled split peas (172429, 65 µg per 100 g at 69.5% water) imply for the dry pea.
- `raw_banana`: the record is USDA's green plantain. Indian raw bananas are cooking varieties that may hold more water, in which case kcal and carbohydrate read high.
- `surmai`: USDA's king mackerel is the Atlantic species (Scomberomorus cavalla); surmai on Indian coasts is S. commerson, from the same genus.
- `pav`: UK flour fortification raises calcium, and probably iron, above Indian pav. The split dals are the gap that matters most: every dal dish will show fibre as incomplete until a source with fibre for split dals is found.
