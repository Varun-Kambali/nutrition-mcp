"""The MCP server: five read-only tools and a sources resource over stdio.

Each tool's signature carries its bounds, so they reach the JSON Schema the model sees and the
SDK rejects a bad argument before the tool runs. The tool then calls one query function and
turns the errors a caller can fix into tool errors. The query layer keeps its own checks,
because the evals call it directly.
"""

import json
import logging
import sys
import time
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, InputRequiredResult, ToolAnnotations
from pydantic import Field, PositiveFloat, ValidationError

from nutrition_mcp import __version__, targets
from nutrition_mcp.config import Settings, describe_settings_error
from nutrition_mcp.loader import DataError, build_registry
from nutrition_mcp.log import configure_logging
from nutrition_mcp.models import Diet, Kind, Region
from nutrition_mcp.queries import (
    MAX_MEAL_ITEMS,
    MAX_RANK_LIMIT,
    RANK_BY,
    meal_totals,
    nutrition_for,
    rank_foods,
)
from nutrition_mcp.registry import Registry, UnknownFood
from nutrition_mcp.schemas import (
    Activity,
    Age,
    Goal,
    HeightCm,
    MealItemIn,
    MealResult,
    NutritionResult,
    Per,
    RankResult,
    SearchResult,
    Sex,
    TargetsIn,
    TargetsResult,
    WeightKg,
)
from nutrition_mcp.search import MAX_QUERY_LENGTH, MAX_SEARCH_LIMIT, MAX_SUGGESTIONS, search_foods

logger = logging.getLogger(__name__)

SOURCES_URI = "nutrition://sources"
TOOL_NAMES = ("search_food", "get_nutrition", "compute_meal", "find_foods", "compute_targets")
# The list a tool's logged count is taken from. The other tools return one result.
_COUNTED_LIST = {"search_food": "results", "find_foods": "results", "compute_meal": "items"}

# Closed world: the tools read in-memory data and make no network calls.
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = (
    "Nutrition data for Indian dishes and ingredients, each value with its source and portion. "
    "Call search_food to get food ids, then get_nutrition for one food or compute_meal for "
    "several. Take every number in an answer from a tool result instead of estimating it."
)

ROUNDING = (
    "Values are rounded to 2 decimals, so 0.0 can be a small amount; a value the source does "
    "not report is null, never 0."
)

SEARCH_FOOD = (
    "Find foods by name and get their ids. Call this first: the other food tools take ids "
    "from it. Spelling variants match (sambar, sambhar), but every word of the query has to "
    "match a word of one name, so a dish that is not in the data returns no results and up to "
    "5 close names as suggestions. Check that a result's name or one of its aliases is the "
    "food asked for; when no result is, report the dish as missing instead of using a "
    "suggestion or another result in its place. Each result has its kind (dish or "
    "ingredient), regions, diet and own measures. `total` counts every match before `limit`; "
    "when it is larger than the results shown, raise `limit` or narrow with region, diet or "
    "kind."
)
GET_NUTRITION = (
    "Nutrients for an amount of one food, with the grams used, the measure and oil "
    "assumptions, glycemic load when the GI is known, and the sources. `unit` is g (always "
    "valid) or a household measure such as katori, piece or tbsp; an unknown unit returns the "
    "valid ones for that food. A nutrient listed in `incomplete` is a lower bound. For more "
    "than one food, use compute_meal. " + ROUNDING
)
COMPUTE_MEAL = (
    "Per-item and total nutrients and glycemic load for a meal of 1 to 30 items, each a food "
    "id, amount and unit as in get_nutrition. Use this for any total instead of adding numbers "
    "yourself. If an item fails, the error names it and no totals are returned. Totals listed "
    "in `total_incomplete` are lower bounds, and `glycemic_load_missing` names the items the "
    "meal's glycemic load leaves out. " + ROUNDING
)
FIND_FOODS = (
    "Rank foods by one nutrient or by glycemic load, per 100 kcal, per 100 g or per serving "
    "(dishes only), for questions such as high-protein vegetarian dishes or iron-rich foods. "
    "Highest first, except lowest first for kcal, sugars, saturated fat, sodium and glycemic "
    "load; `order` says which. Foods with no value for the ranked nutrient are left out and "
    "counted in `skipped_missing`, and so is a dish with an incomplete value for the ranked "
    "nutrient. Values are rounded to 2 decimals, so 0.0 can be a small amount."
)
COMPUTE_TARGETS = (
    "Estimated daily calorie and protein targets for an adult, from the Mifflin-St Jeor "
    "equation with an activity multiplier and a goal adjustment. Use it when asked how much "
    "someone should eat in a day. The result shows the formula and the inputs, applies a "
    "calorie floor, and is an estimate, not medical advice. Nothing is stored."
)

FoodId = Annotated[str, Field(description="A food id from search_food, such as toor_dal.")]
Amount = Annotated[PositiveFloat, Field(description="How many of `unit`; above 0.")]
Unit = Annotated[str, Field(description="g, or a household measure such as katori or piece.")]
RegionFilter = Annotated[
    Region | None, Field(description="Keep dishes from this region; ingredients have none.")
]
DietFilter = Annotated[
    Diet | None, Field(description="Keep foods suitable for this diet: veg includes vegan.")
]
KindFilter = Annotated[Kind | None, Field(description="Keep only dishes or only ingredients.")]


def _close_matches(reg: Registry, food_id: str) -> str:
    """Foods with a name close to an unknown ID, worded for the error message."""
    fallback = "search_food finds food ids by name"
    try:
        found = search_foods(reg, food_id.replace("_", " "), None, None, None, MAX_SUGGESTIONS)
    except ValueError:
        # An ID with no letters or digits, or one too long to search for.
        return fallback
    if found.results:
        return "close matches: " + ", ".join(food.id for food in found.results)
    if found.suggestions:
        return f"close names: {', '.join(found.suggestions)} (search_food gives their ids)"
    return fallback


def _explain(reg: Registry, err: Exception) -> str:
    if isinstance(err, UnknownFood):
        food_id = str(err.args[0])
        return f"unknown food id '{food_id}'; {_close_matches(reg, food_id)}"
    if isinstance(err.__cause__, UnknownFood):
        # meal_totals has already named the item; the close matches go after it.
        return f"{err}; {_close_matches(reg, str(err.__cause__.args[0]))}"
    # A UnitError lists the valid units, and any other ValueError names the bound it broke.
    return str(err)


def _answer[T](reg: Registry, query: Callable[[], T]) -> T:
    """Run a query, turning the errors a caller can fix into tool errors."""
    try:
        return query()
    except (UnknownFood, ValueError) as err:
        raise ToolError(_explain(reg, err)) from err


def _count(name: str, result: object) -> int:
    """Results returned, for the log line. It cannot raise, so it cannot fail a good call."""
    content = result.structured_content if isinstance(result, CallToolResult) else None
    if not isinstance(content, dict) or not content:
        return 0
    key = _COUNTED_LIST.get(name)
    if key is None:
        return 1
    listed = content.get(key)
    return len(listed) if isinstance(listed, list) else 0


class NutritionServer(MCPServer[Any]):
    """MCPServer that logs one JSON line per tool call: name, latency, outcome and count.

    The line is written here, around the SDK's own `call_tool`, so a call the SDK rejects
    against the schema before any tool runs is logged too.
    """

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *args: Any, **kwargs: Any
    ) -> CallToolResult | InputRequiredResult:
        # Everything after the arguments goes through untouched: uvx installs the newest mcp
        # 2.x, and a parameter added there must not break every call.
        started = time.perf_counter()
        try:
            result = await super().call_tool(name, arguments, *args, **kwargs)
        except BaseException:
            _log_call(name, started, "error", 0)
            raise
        _log_call(name, started, "ok", _count(name, result))
        return result


def _log_call(name: str, started: float, outcome: str, count: int) -> None:
    logger.info(
        "tool call",
        extra={
            # A name the client made up is not logged, since it is the caller's text.
            "tool": name if name in TOOL_NAMES else "unknown",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "outcome": outcome,
            "count": count,
        },
    )


def build_server(reg: Registry, settings: Settings) -> MCPServer[Any]:
    """The server with its five tools and the sources resource, over the given registry."""
    server = NutritionServer("nutrition-mcp", version=__version__, instructions=INSTRUCTIONS)

    @server.tool(description=SEARCH_FOOD, annotations=READ_ONLY)
    def search_food(
        query: Annotated[str, Field(max_length=MAX_QUERY_LENGTH, description="A food name.")],
        region: RegionFilter = None,
        diet: DietFilter = None,
        kind: KindFilter = None,
        limit: Annotated[int, Field(ge=1, le=MAX_SEARCH_LIMIT)] = 10,
    ) -> SearchResult:
        return _answer(reg, lambda: search_foods(reg, query, region, diet, kind, limit))

    @server.tool(description=GET_NUTRITION, annotations=READ_ONLY)
    def get_nutrition(food_id: FoodId, amount: Amount, unit: Unit) -> NutritionResult:
        return _answer(reg, lambda: nutrition_for(reg, settings, food_id, amount, unit))

    @server.tool(description=COMPUTE_MEAL, annotations=READ_ONLY)
    def compute_meal(
        items: Annotated[list[MealItemIn], Field(min_length=1, max_length=MAX_MEAL_ITEMS)],
    ) -> MealResult:
        return _answer(reg, lambda: meal_totals(reg, settings, items))

    @server.tool(description=FIND_FOODS, annotations=READ_ONLY)
    def find_foods(
        # The query layer checks the value; the enum shows the model what it can rank by.
        rank_by: Annotated[str, Field(json_schema_extra={"enum": list(RANK_BY)})],
        per: Per = "100kcal",
        region: RegionFilter = None,
        diet: DietFilter = None,
        kind: KindFilter = None,
        limit: Annotated[int, Field(ge=1, le=MAX_RANK_LIMIT)] = 10,
    ) -> RankResult:
        return _answer(reg, lambda: rank_foods(reg, rank_by, per, region, diet, kind, limit))

    @server.tool(description=COMPUTE_TARGETS, annotations=READ_ONLY)
    def compute_targets(
        sex: Sex,
        age: Age,
        height_cm: HeightCm,
        weight_kg: WeightKg,
        activity: Activity,
        goal: Goal,
    ) -> TargetsResult:
        person = TargetsIn(
            sex=sex,
            age=age,
            height_cm=height_cm,
            weight_kg=weight_kg,
            activity=activity,
            goal=goal,
        )
        return _answer(reg, lambda: targets.compute_targets(person))

    @server.resource(
        SOURCES_URI,
        name="sources",
        description="Every loaded data source: name, URL, license, version, retrieval date "
        "and the attribution its license asks for.",
        mime_type="application/json",
    )
    def sources() -> str:
        return json.dumps([source.model_dump(mode="json") for source in reg.sources()], indent=2)

    return server


def main() -> None:
    """Load the data and serve the tools on stdio, or exit 1 naming the bad setting or file."""
    try:
        settings = Settings()
    except ValidationError as err:
        # The log level is one of the settings, so the error goes out at the default level.
        configure_logging("INFO")
        logger.error("settings error: %s", describe_settings_error(err))
        sys.exit(1)
    configure_logging(settings.log_level)
    try:
        reg = build_registry(settings.data_dirs)
    except DataError as err:
        logger.error("dataset error: %s", err)
        sys.exit(1)
    build_server(reg, settings).run()
