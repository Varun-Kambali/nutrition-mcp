# Licenses for the bundled data

The code in this repository is MIT licensed (see `LICENSE` at the repository root). The files in this folder carry the licenses of the sources they come from. Each row of `ingredients.csv` names its source in `source_id`, and `sources.yaml` holds the full entry for each source.

| File | Source (`source_id`) | License | Attribution |
|---|---|---|---|
| `ingredients.csv`, rows with `usda_fdc` | USDA FoodData Central, SR Legacy | Public domain | U.S. Department of Agriculture, Agricultural Research Service. FoodData Central. fdc.nal.usda.gov. |
| `ingredients.csv`, rows with `cofid_2021` | McCance and Widdowson's Composition of Foods Integrated Dataset (CoFID) 2021, Public Health England | [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) | Contains public sector information licensed under the Open Government Licence v3.0. |
| `dishes/*.yaml` (`recipes_cc_by`) | Standard recipes written for this project | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Varun Kambali, nutrition-mcp |
| `gi` and `gi_ref` values (`gi_compendium_2021`) | Henry CJ, Quek RYC, Kaur B, Shyam S, Singh HKG. A glycaemic index compendium of non-western foods. Nutrition & Diabetes 11, 2 (2021). doi:10.1038/s41387-020-00145-w | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | The citation in the previous column |
| `sources.yaml`, this file | Written for this project | CC BY 4.0 | Varun Kambali, nutrition-mcp |

## Changes made to the source data

- Values are per 100 g of the food as described in the source record. Each row keeps the source record ID in `source_record`, so a value can be checked against the original.
- USDA carbohydrate by difference has fibre subtracted, to give available carbohydrate.
- CoFID "Tr" (trace) is stored as 0. "N" (present in significant amounts, but CoFID has no reliable value) and blank cells are stored as empty cells.
- Names, aliases and diet tags were added for this project.
- `density_g_per_ml` is computed from a USDA cup or tablespoon portion weight.

`docs/data.md` in the repository describes each mapping in full.
