import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, field_validator

from nutrition_mcp.nutrients import NUTRIENT_KEYS

Diet = Literal["veg", "egg", "non_veg", "vegan"]
Region = Literal["north", "south", "east", "west"]

SLUG = r"^[a-z0-9]+(_[a-z0-9]+)*$"
Slug = Annotated[str, Field(pattern=SLUG)]

# Word boundaries keep "boiled" or "foil" from passing as an oil assumption.
_FAT_ASSUMPTION = re.compile(r"\b(?:oils?|ghee|no added fat)\b", re.IGNORECASE)

Amount = Annotated[float | None, Field(ge=0)]


class Source(BaseModel):
    id: Slug
    name: str
    url: str
    license: str
    version: str
    retrieved: date
    attribution: str


class Nutrients(BaseModel):
    """Per-100 g amounts. None means the source did not report it, which is not the same as 0."""

    model_config = ConfigDict(extra="forbid")

    kcal: Amount = None
    protein_g: Amount = None
    fat_g: Amount = None
    sat_fat_g: Amount = None
    carbs_available_g: Amount = None
    sugars_g: Amount = None
    fibre_g: Amount = None
    sodium_mg: Amount = None
    iron_mg: Amount = None
    calcium_mg: Amount = None
    zinc_mg: Amount = None
    folate_ug: Amount = None
    vit_a_ug: Amount = None
    vit_b12_ug: Amount = None
    vit_c_mg: Amount = None
    vit_d_ug: Amount = None

    def scaled(self, factor: float) -> "Nutrients":
        return Nutrients.model_validate(
            {key: None if value is None else value * factor for key, value in self}
        )

    def get(self, key: str) -> float | None:
        # getattr alone would also return methods such as "scaled".
        if key not in NUTRIENT_KEYS:
            raise KeyError(key)
        value: float | None = getattr(self, key)
        return value


class Ingredient(BaseModel):
    id: Slug
    name: str
    aliases: list[str]
    diet: Diet
    source_id: str
    source_record: str
    per_100g: Nutrients
    gi: Annotated[float, Field(ge=0, le=110)] | None = None
    gi_ref: str | None = None
    density_g_per_ml: PositiveFloat | None = None


class RecipeItem(BaseModel):
    ingredient_id: str
    grams: PositiveFloat


class Dish(BaseModel):
    id: Slug
    name: str
    aliases: list[str]
    regions: Annotated[list[Region], Field(min_length=1, max_length=4)]
    diet: Diet
    servings: Annotated[int, Field(ge=1)]
    cooked_weight_g: PositiveFloat
    ingredients: Annotated[list[RecipeItem], Field(min_length=1)]
    measures: dict[str, PositiveFloat]
    assumptions: str
    gi: Annotated[float, Field(ge=0, le=110)] | None = None
    gi_ref: str | None = None
    source_id: str

    @field_validator("regions")
    @classmethod
    def _regions_unique(cls, regions: list[Region]) -> list[Region]:
        if len(set(regions)) != len(regions):
            raise ValueError("regions must not repeat")
        return regions

    @field_validator("assumptions")
    @classmethod
    def _assumptions_state_the_fat(cls, assumptions: str) -> str:
        if not _FAT_ASSUMPTION.search(assumptions):
            raise ValueError('assumptions must state the fat used: "oil", "ghee" or "no added fat"')
        return assumptions


class Food(BaseModel):
    """An ingredient or a dish as the query functions see it, with dish nutrients computed."""

    id: str
    kind: Literal["ingredient", "dish"]
    name: str
    aliases: list[str]
    regions: list[Region]
    diet: Diet
    source_ids: list[str]
    per_100g: Nutrients
    incomplete: dict[str, list[str]]
    gi: float | None
    gi_ref: str | None
    measures: dict[str, float]
    serving_g: float | None
    assumptions: str | None
    density_g_per_ml: float | None
