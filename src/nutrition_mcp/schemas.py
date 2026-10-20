"""Inputs and outputs of the query functions, which the MCP tools expose as structured output."""

from typing import Literal, Self

from pydantic import BaseModel

from nutrition_mcp.models import Diet, Food, Kind, Nutrients, Region

Per = Literal["100kcal", "100g", "serving"]
Order = Literal["desc", "asc"]


class FoodSummary(BaseModel):
    """Enough to identify a food and to pick a unit that fits it."""

    id: str
    kind: Kind
    name: str
    aliases: list[str]
    regions: list[Region]
    diet: Diet
    source_ids: list[str]
    # The food's own household measures in grams, so a client can choose a valid unit.
    measures: dict[str, float]

    @classmethod
    def from_food(cls, food: Food) -> Self:
        return cls(
            id=food.id,
            kind=food.kind,
            name=food.name,
            aliases=food.aliases,
            regions=food.regions,
            diet=food.diet,
            source_ids=food.source_ids,
            measures={name: round(grams, 2) for name, grams in food.measures.items()},
        )


class MealItemIn(BaseModel):
    food_id: str
    amount: float
    unit: str


class NutritionResult(BaseModel):
    """The nutrients in one amount of one food, with how the amount was worked out."""

    food: FoodSummary
    amount: float
    unit: str
    grams: float
    measure_assumption: str
    nutrients: Nutrients
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
    total: Nutrients
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
