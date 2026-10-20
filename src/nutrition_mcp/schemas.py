"""Inputs and outputs of the query functions, which the MCP tools expose as structured output."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, PositiveFloat

from nutrition_mcp.models import Diet, Food, Kind, Nutrients, Region
from nutrition_mcp.units import unit_grams

# Decimal places of every nutrient value, gram amount and rank value in a result.
DECIMALS = 2

Per = Literal["100kcal", "100g", "serving"]
Order = Literal["desc", "asc"]
Sex = Literal["male", "female"]
Activity = Literal["sedentary", "light", "moderate", "active", "very_active"]
Goal = Literal["lose", "maintain", "gain"]

# Adults only. TargetsIn and the compute_targets tool share these, so the bounds the model
# sees in the tool schema are the ones the calculation enforces.
Age = Annotated[int, Field(ge=18, le=100)]
HeightCm = Annotated[float, Field(ge=120, le=230)]
WeightKg = Annotated[float, Field(ge=30, le=250)]


class FoodSummary(BaseModel):
    """Enough to identify a food and to pick a unit that fits it."""

    id: str
    kind: Kind
    name: str
    aliases: list[str]
    regions: list[Region]
    diet: Diet
    source_ids: list[str]
    units: dict[str, float] = Field(
        description="Every unit get_nutrition and compute_meal accept for this food, with the "
        "grams in one of each: g, the food's own measures, then the default katori for a dish "
        "served by the bowl and the volume units for a liquid."
    )

    @classmethod
    def from_food(cls, food: Food, default_katori_g: float) -> Self:
        return cls(
            id=food.id,
            kind=food.kind,
            name=food.name,
            aliases=food.aliases,
            regions=food.regions,
            diet=food.diet,
            source_ids=food.source_ids,
            units={
                name: round(grams, DECIMALS)
                for name, grams in unit_grams(food, default_katori_g).items()
            },
        )


class MealItemIn(BaseModel):
    food_id: str
    # Above 0 here so the compute_meal schema shows the bound; to_grams checks it again.
    amount: PositiveFloat
    unit: str


class NutritionResult(BaseModel):
    """The nutrients in one amount of one food, with how the amount was worked out."""

    food: FoodSummary
    amount: float
    unit: str
    grams: float
    measure_assumption: str
    nutrients: Nutrients = Field(description="Nutrient amounts in `grams` of this food.")
    # For each nutrient, the ingredients with no value for it. The nutrient is then a floor.
    incomplete: dict[str, list[str]]
    # Nutrients whose dish value is a raw-ingredient total, because v1 applies no cooking losses.
    raw_ingredient_totals: list[str]
    gi: float | None
    gi_ref: str | None
    glycemic_load: float | None
    oil_assumption: str | None
    sources: list[str]


class MealResult(BaseModel):
    items: list[NutritionResult]
    total: Nutrients = Field(description="Nutrient amounts, the sum over the items.")
    total_incomplete: dict[str, list[str]]
    glycemic_load: float | None
    # Foods with no glycemic load, so the meal's load covers only the other items.
    glycemic_load_missing: list[str]


class RankedFood(BaseModel):
    food: FoodSummary
    value: float


class RankResult(BaseModel):
    rank_by: str
    per: Per
    order: Order
    results: list[RankedFood]
    # Foods left out because they have no usable value for what was ranked.
    skipped_missing: int


class SearchResult(BaseModel):
    query: str
    # Best match first. Each carries its kind and regions, so an ambiguous name stays a list.
    results: list[FoodSummary]
    # How many foods matched before the limit cut the list, so a caller can tell it was cut.
    total: int
    # Close food names, given only when nothing matched well enough to be a result.
    suggestions: list[str]


class TargetsIn(BaseModel):
    """A person's details for one targets calculation. Nothing is stored."""

    sex: Sex
    age: Age
    height_cm: HeightCm
    weight_kg: WeightKg
    activity: Activity
    goal: Goal


class TargetsResult(BaseModel):
    kcal: int
    protein_g: int
    bmr_kcal: int
    formula: str
    inputs: TargetsIn
    notes: list[str]
