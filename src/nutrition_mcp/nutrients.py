from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NutrientSpec:
    key: str
    label: str
    unit: str
    lower_is_better: bool


# Order here is the order of the columns in ingredients.csv and of the fields on Nutrients.
NUTRIENTS: tuple[NutrientSpec, ...] = (
    NutrientSpec("kcal", "Energy", "kcal", True),
    NutrientSpec("protein_g", "Protein", "g", False),
    NutrientSpec("fat_g", "Total fat", "g", False),
    NutrientSpec("sat_fat_g", "Saturated fat", "g", True),
    NutrientSpec("carbs_available_g", "Available carbohydrate", "g", False),
    NutrientSpec("sugars_g", "Total sugars", "g", True),
    NutrientSpec("fibre_g", "Fibre", "g", False),
    NutrientSpec("sodium_mg", "Sodium", "mg", True),
    NutrientSpec("iron_mg", "Iron", "mg", False),
    NutrientSpec("calcium_mg", "Calcium", "mg", False),
    NutrientSpec("zinc_mg", "Zinc", "mg", False),
    NutrientSpec("folate_ug", "Folate", "µg", False),
    NutrientSpec("vit_a_ug", "Vitamin A", "µg", False),
    NutrientSpec("vit_b12_ug", "Vitamin B12", "µg", False),
    NutrientSpec("vit_c_mg", "Vitamin C", "mg", False),
    NutrientSpec("vit_d_ug", "Vitamin D", "µg", False),
)

NUTRIENT_KEYS: tuple[str, ...] = tuple(spec.key for spec in NUTRIENTS)

# Dish values for these are raw-ingredient totals, because v1 applies no cooking losses.
HEAT_SENSITIVE: frozenset[str] = frozenset({"folate_ug", "vit_c_mg", "vit_b12_ug"})

# The nutrients the data-accuracy evaluation compares with independent dish records, in the
# order of their columns in evals/datasets/references.csv. scripts/build_references.py writes
# those columns and evals/run_evals.py reads them, both from this tuple.
REFERENCE_NUTRIENTS: tuple[str, ...] = (
    "kcal",
    "protein_g",
    "carbs_available_g",
    "iron_mg",
    "calcium_mg",
)
