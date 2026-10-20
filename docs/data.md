# Data notes

How the bundled ingredient table is built, how each source's values map onto the server's nutrients, where the two sources define a nutrient differently, and where the glycemic index values come from. The licenses and attribution text for every data file are in [`src/nutrition_mcp/data/bundled/LICENSE.md`](../src/nutrition_mcp/data/bundled/LICENSE.md). Why these two sources were chosen is in [ADR 0002](decisions/0002-data-sources.md).

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
4. Prints a review table: the ingredient's name in the dataset, the source, the record and the source's own description, kcal, protein and the number of missing nutrients.

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

## Glycemic index

GI values come from one source: Henry CJ, Quek RYC, Kaur B, Shyam S, Singh HKG, "A glycaemic index compendium of non-western foods", Nutrition & Diabetes 11, 2 (2021), doi:10.1038/s41387-020-00145-w, CC BY 4.0 (`gi_compendium_2021` in `sources.yaml`). Its Table 1 lists 940 foods from 159 studies. Glycemic load is computed from the GI by `nutrition.glycemic_load`: GI x available carbohydrate in grams / 100, rounded to 1 decimal, and missing when either input is missing.

### Extracting the Indian rows

`make gi` runs `scripts/extract_gi.py`. It downloads the paper's full text from Europe PMC once into `.cache/`, checks its SHA-256 (a changed file stops the run), reads Table 1 and writes `data_build/gi_compendium_india.csv`. A row is kept when all three hold:

1. **Indian.** The study was done in India, or the food name is an Indian dish. The country has to be exactly "India": the table's "West India" rows come from a study of Caribbean foods. Rows from other countries count when they name a dish such as idli, dosa, upma, chapati, roti, khichdi, biryani or samosa, including the Sri Lankan names for shared South Indian dishes (thosai, pittu, hopper, string hopper, sampar).
2. **Glucose scale.** The GI column is headed "glucose = 100", but some rows name white bread as the reference food. Ref 153 reports the same Sri Lankan breads against both glucose and white bread, and white bread scores 100 on the second, so those rows are on a white bread scale. The paper gives no factor to convert them, so they are dropped. Dextrose counts as glucose.
3. **One number.** The GI cell holds a single value.

The script keeps 176 of the 940 rows: 118 tested in India and 58 tested elsewhere that name an Indian dish. It drops 18 rows that pass the first test, and prints each one with the reason: 16 on a white bread scale (among them T1-667, a rice dosa tested in India), T1-713, whose reference food is Glucodin, a product the paper does not describe, and T1-015, a basmati rice row with one value per ethnic group.

| Column | Holds |
|---|---|
| `row_id` | `T1-` and the row number printed in Table 1, so `T1-698` is row 698 |
| `table` | The paper's table number, always 1 |
| `food`, `country` | As printed |
| `participants` | Who was tested, from the paper's participants column: `healthy`, `type 2 diabetes` (the paper writes T2DM or NIDDM) or `not stated` |
| `gi` | The mean GI as printed, with trailing zeros dropped (68.70 is written 68.7) |
| `reference` | The paper's reference number for the study, with the study's DOI where the paper gives one |
| `notes` | The reference food and test period as printed. A row a bundled dish uses also names the dish, the candidate rows considered and why this one was chosen |

The notes for matched rows live in `MATCH_NOTES` in the script, so `make gi` writes the same file every time. A test checks that the rows with notes and the dishes that use them agree.

### Matching a food to a row

GI belongs to food as it is eaten: dishes, and ingredients that are eaten as they are, such as pav, milk or curd, when the compendium lists the same food. Raw grains and dals never get one, because their GI says nothing about the cooked dish. The compendium has no Indian row for pav, milk or curd, so no ingredient has a GI.

A dish gets a GI only when a row is the same food with a similar preparation: the same main ingredients and the same cooking method. A dish the compendium does not name is not given the value of a similar dish. When several rows qualify, they are narrowed in order. Rows measured in healthy participants come first, because the ISO 26642 method for measuring GI tests healthy participants, so a value from people with type 2 diabetes was not measured the standard way. Next, rows from studies done in India come first. Among the rows left, the median row is used: an actual row, and with an even number of rows the lower of the two middle values. A dish with no such row keeps `gi` and `gi_ref` empty, and its glycemic load is missing.

`gi_ref` records the match as `"<source_id>: <compendium food> (Table <n>, <row_id>)"`, for example `"gi_compendium_2021: Idli (Table 1, T1-698)"`. The loader checks that the source ID names a loaded source and lists that source with the food's other sources. `tests/integration/test_bundled_dishes.py` checks that every bundled `gi_ref` names a row of the CSV with the same food, table and GI.

7 of the 41 dishes have a GI:

| Dish | Row | Compendium food | GI | Candidates and choice |
|---|---|---|---|---|
| `idli` | T1-698 | Idli | 62.45 | T1-698 (62.45) and T1-627 (67.11); with two rows, the lower |
| `plain_dosa` | T1-688 | Dosa | 55.8 | The only Indian dosa row on the glucose scale; T1-667 is on a white bread scale |
| `rava_upma` | T1-697 | Semolina upma | 62.37 | T1-658 (58), T1-697 (62.37), T1-659 (67); the median |
| `chapati` | T1-673 | Wheat chapatti | 48.37 | T1-711 (44.6), T1-673 (48.37), T1-691 (54.43), T1-657 (64), all in healthy participants; with four rows, the lower middle. T1-631 (83.92) was measured in type 2 diabetes and is not used |
| `methi_thepla` | T1-699 | Thepla | 57.77 | The only plain thepla row. The paper does not say whether it had methi leaves |
| `steamed_rice` | T1-666 | White rice | 79.2 | The only plain white rice row. Parboiled, basmati and high-fibre rice rows are other foods |
| `bengali_khichuri` | T1-664 | Rice khichdi | 62.5 | The only rice khichdi row. The paper does not name the dal, and the recipe in this dataset adds potato, cauliflower and peas |

All seven rows were measured in healthy participants. Three of the seven values (idli, dosa and upma) come from one Kerala study, ref 142.

The other 34 dishes have no GI:

- **Rice cooked with fat, curd or a tempering** (jeera rice, lemon rice, curd rice): the compendium has no row for these preparations, and fat and curd change how fast the rice is digested, so plain rice's value is not used.
- **Rice and dal dishes the compendium does not list** (ven pongal, bisi bele bath, varan bhaat). Pongal is close to khichdi, but a dish the compendium does not name gets no value. Bisi bele bath also adds toor dal, tamarind and vegetables. Varan bhaat is plain rice served with a toor dal varan, a meal with no row.
- **Plain paratha**: the nearest rows are porotta (layered maida, T1-696) and misi parantha (with gram flour, T1-634), which have other main ingredients.
- **Sabudana khichdi**: the only tapioca row (T1-687) is the root, not sago pearls.
- **Dals, curries, sabzis and snacks** (dal tadka, dal makhani, musur dal, cholar dal, dalma, rajma masala, chana masala, ghugni, sambar, rasam, both kadhis, palak paneer, aloo gobi, aloo posto, begun bhaja, shukto, zunka, dimer dalna, machher jhol, Goan fish curry, coconut chutney, dhokla, batata vada, misal pav and pav bhaji): the compendium has no row for any of them eaten on its own. Dal, sambar and curries appear only inside mixed meals, such as T1-077 (flatbread with dhal curry) and T1-811 (thosai and sampar), whose GI belongs to the whole meal.
