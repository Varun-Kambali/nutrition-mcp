import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, TextResourceContents

from nutrition_mcp import __version__
from nutrition_mcp import server as server_module
from nutrition_mcp.config import Settings
from nutrition_mcp.loader import build_registry
from nutrition_mcp.log import configure_logging
from nutrition_mcp.registry import Registry
from nutrition_mcp.schemas import TargetsIn
from nutrition_mcp.server import SOURCES_URI, TOOL_NAMES, _count, build_server
from nutrition_mcp.targets import compute_targets

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent.parent / "fixtures"
SETTINGS = Settings(default_katori_g=150.0)
RUN_MAIN = "from nutrition_mcp.server import main; main()"

PERSON: dict[str, Any] = {
    "sex": "male",
    "age": 30,
    "height_cm": 175,
    "weight_kg": 70,
    "activity": "moderate",
    "goal": "maintain",
}


@pytest.fixture
def reg(no_bundled_data: Path) -> Registry:
    # mini_dataset and query_dataset: test_dal, test_roti, test_curry and their ingredients.
    return build_registry([FIXTURES / "mini_dataset", FIXTURES / "query_dataset"])


@pytest.fixture
async def client(reg: Registry) -> Any:
    async with Client(build_server(reg, SETTINGS)) as connected:
        yield connected


def error_text(result: CallToolResult) -> str:
    assert result.is_error
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def ok(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


# Tools listed


async def test_server_names_itself_and_says_how_to_use_the_tools(client: Client) -> None:
    assert client.server_info is not None
    assert (client.server_info.name, client.server_info.version) == ("nutrition-mcp", __version__)
    assert "search_food" in (client.instructions or "")


async def test_lists_five_tools_read_only(client: Client) -> None:
    tools = (await client.list_tools()).tools
    assert {tool.name for tool in tools} == set(TOOL_NAMES)
    assert len(tools) == 5
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False
        assert tool.description
        assert tool.output_schema is not None


async def test_schemas_show_the_bounds(client: Client) -> None:
    schemas = {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}
    nutrition = schemas["get_nutrition"]["properties"]
    assert nutrition["amount"]["exclusiveMinimum"] == 0
    search = schemas["search_food"]["properties"]
    assert (search["limit"]["minimum"], search["limit"]["maximum"]) == (1, 50)
    find = schemas["find_foods"]["properties"]
    assert (find["limit"]["minimum"], find["limit"]["maximum"]) == (1, 50)
    assert set(find["per"]["enum"]) == {"100kcal", "100g", "serving"}
    assert "glycemic_load" in find["rank_by"]["enum"]
    meal = schemas["compute_meal"]
    assert (meal["properties"]["items"]["minItems"], meal["properties"]["items"]["maxItems"]) == (
        1,
        30,
    )
    assert meal["$defs"]["MealItemIn"]["properties"]["amount"]["exclusiveMinimum"] == 0
    person = schemas["compute_targets"]["properties"]
    assert (person["age"]["minimum"], person["age"]["maximum"]) == (18, 100)
    assert set(person["sex"]["enum"]) == {"male", "female"}


async def test_food_id_and_unit_are_described_and_capped_at_64_characters(client: Client) -> None:
    schemas = {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}
    meal_item = schemas["compute_meal"]["$defs"]["MealItemIn"]["properties"]
    for fields in (schemas["get_nutrition"]["properties"], meal_item):
        for name in ("food_id", "unit"):
            assert fields[name]["maxLength"] == 64
        for name in ("food_id", "amount", "unit"):
            assert fields[name]["description"]
    assert (
        meal_item["food_id"]["description"]
        == (schemas["get_nutrition"]["properties"]["food_id"]["description"])
    )


async def test_a_food_id_or_unit_over_64_characters_is_rejected(client: Client) -> None:
    long = "x" * 65
    nutrition = {"food_id": "test_dal", "amount": 1, "unit": "g"}
    for arguments in ({**nutrition, "food_id": long}, {**nutrition, "unit": long}):
        # Rejected against the schema before the tool runs, not as an unknown id or unit.
        single = error_text(await client.call_tool("get_nutrition", arguments))
        assert "at most 64 characters" in single
        meal = error_text(await client.call_tool("compute_meal", {"items": [arguments]}))
        assert "at most 64 characters" in meal


async def test_output_schemas_describe_nutrients_by_their_basis(client: Client) -> None:
    # The values are for the amount asked about or the meal, not per 100 g.
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    nutrition = tools["get_nutrition"].output_schema
    assert nutrition is not None
    assert "Per-100 g" not in json.dumps(nutrition)
    assert "null means not reported" in nutrition["$defs"]["Nutrients"]["description"]
    assert "in `grams` of this food" in nutrition["properties"]["nutrients"]["description"]
    meal = tools["compute_meal"].output_schema
    assert meal is not None
    assert "Per-100 g" not in json.dumps(meal)
    assert "sum over the items" in meal["properties"]["total"]["description"]


async def test_descriptions_say_values_are_rounded(client: Client) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    for name in ("get_nutrition", "compute_meal", "find_foods"):
        assert "2 decimals" in (tools[name].description or "")


async def test_descriptions_point_to_the_right_tool_and_name_skipped_foods(client: Client) -> None:
    tools = {tool.name: tool.description or "" for tool in (await client.list_tools()).tools}
    assert "use compute_meal" in tools["get_nutrition"]
    assert "incomplete value for the ranked nutrient" in tools["find_foods"]


async def test_search_description_says_to_check_the_result_is_the_food_asked_for(
    client: Client,
) -> None:
    tools = {tool.name: tool.description or "" for tool in (await client.list_tools()).tools}
    assert "name or one of its aliases is the food asked for" in tools["search_food"]
    assert "report the dish as missing" in tools["search_food"]


# search_food


async def test_search_food_returns_ids_and_total(client: Client) -> None:
    found = ok(await client.call_tool("search_food", {"query": "roti", "limit": 1}))
    assert [food["id"] for food in found["results"]] == ["test_roti"]
    # The units get_nutrition accepts for the roti, with grams in one of each.
    assert found["results"][0]["units"] == {"g": 1.0, "piece": 40.0, "roti": 40.0, "serving": 40.0}
    assert found["total"] >= 1
    assert found["suggestions"] == []


async def test_search_food_filters_by_kind(client: Client) -> None:
    found = ok(await client.call_tool("search_food", {"query": "toor dal", "kind": "ingredient"}))
    assert {food["kind"] for food in found["results"]} == {"ingredient"}


async def test_search_food_rejects_an_unknown_region(client: Client) -> None:
    result = await client.call_tool("search_food", {"query": "dal", "region": "central"})
    assert "region" in error_text(result)


async def test_region_with_ingredients_is_a_tool_error(client: Client) -> None:
    for tool, arguments in (
        ("search_food", {"query": "dal", "region": "north", "kind": "ingredient"}),
        ("find_foods", {"rank_by": "iron_mg", "region": "north", "kind": "ingredient"}),
    ):
        text = error_text(await client.call_tool(tool, arguments))
        assert "ingredients have no region; drop region or use kind='dish'" in text


# get_nutrition


async def test_get_nutrition_structured(client: Client) -> None:
    result = ok(
        await client.call_tool("get_nutrition", {"food_id": "test_dal", "amount": 200, "unit": "g"})
    )
    assert result["nutrients"]["kcal"] == 171.5
    assert result["nutrients"]["protein_g"] == 11.0
    assert result["grams"] == 200.0
    assert result["food"]["id"] == "test_dal"
    assert result["sources"]


async def test_amount_as_string_coerces(client: Client) -> None:
    # Some clients send numbers as strings; "2" is still two rotis.
    result = ok(
        await client.call_tool(
            "get_nutrition", {"food_id": "test_roti", "amount": "2", "unit": "piece"}
        )
    )
    assert result["grams"] == 80.0
    assert result["amount"] == 2.0


async def test_zero_amount_is_tool_error(client: Client) -> None:
    result = await client.call_tool(
        "get_nutrition", {"food_id": "test_dal", "amount": 0, "unit": "g"}
    )
    assert "greater than 0" in error_text(result)


async def test_amount_over_the_gram_limit_is_tool_error(client: Client) -> None:
    result = await client.call_tool(
        "get_nutrition", {"food_id": "test_dal", "amount": 6000, "unit": "g"}
    )
    assert "over the limit of 5000 g" in error_text(result)


async def test_unknown_unit_lists_valid_units(client: Client) -> None:
    result = await client.call_tool(
        "get_nutrition", {"food_id": "test_roti", "amount": 1, "unit": "katori"}
    )
    text = error_text(result)
    assert "unknown unit 'katori'" in text
    assert "valid units: g, piece, roti" in text


async def test_unknown_food_suggests(client: Client) -> None:
    result = await client.call_tool(
        "get_nutrition", {"food_id": "test_dall", "amount": 1, "unit": "g"}
    )
    text = error_text(result)
    assert "unknown food id 'test_dall'" in text
    assert "close matches: test_dal" in text


async def test_unknown_food_with_only_close_names_gives_names(client: Client) -> None:
    # "chicken curry" is not a whole-word match for any food, so search has names, not ids.
    result = await client.call_tool(
        "get_nutrition", {"food_id": "chicken_curry", "amount": 1, "unit": "g"}
    )
    text = error_text(result)
    assert "close names: Chicken breast (raw) (search_food gives their ids)" in text


async def test_a_missing_dish_is_not_matched_to_a_different_dish() -> None:
    # On the bundled data, chana masala has the alias "chickpea curry", which scores 88
    # against "chicken curry" as a whole string. It is a different dish, so it must not come
    # back as a close match (an id to use); it can still be a close name.
    async with Client(build_server(build_registry([]), SETTINGS)) as bundled:
        result = await bundled.call_tool(
            "get_nutrition", {"food_id": "chicken_curry", "amount": 1, "unit": "g"}
        )
    text = error_text(result)
    assert "close matches" not in text
    assert "chana_masala" not in text


async def test_unknown_food_with_no_close_match_points_to_search(client: Client) -> None:
    result = await client.call_tool("get_nutrition", {"food_id": "xyzzy", "amount": 1, "unit": "g"})
    text = error_text(result)
    assert "unknown food id 'xyzzy'" in text
    assert "search_food" in text


async def test_unknown_food_with_no_letters_still_explains(client: Client) -> None:
    # search_foods rejects a query with no letters or digits; the error must still come back.
    result = await client.call_tool("get_nutrition", {"food_id": "__", "amount": 1, "unit": "g"})
    assert "unknown food id '__'" in error_text(result)


# compute_meal


async def test_compute_meal_totals(client: Client) -> None:
    items = [
        {"food_id": "test_dal", "amount": 200, "unit": "g"},
        {"food_id": "test_roti", "amount": 2, "unit": "piece"},
    ]
    meal = ok(await client.call_tool("compute_meal", {"items": items}))
    assert [item["food"]["id"] for item in meal["items"]] == ["test_dal", "test_roti"]
    # 171.5 + 226.67 kcal, and 11 + 8 g protein, as in the query tests.
    assert meal["total"]["kcal"] == pytest.approx(398.17)
    assert meal["total"]["protein_g"] == pytest.approx(19.0)


async def test_compute_meal_names_the_failing_item(client: Client) -> None:
    items = [
        {"food_id": "test_dal", "amount": 100, "unit": "g"},
        {"food_id": "test_rotti", "amount": 1, "unit": "piece"},
    ]
    text = error_text(await client.call_tool("compute_meal", {"items": items}))
    assert "item 2 (test_rotti): unknown food id 'test_rotti'" in text
    assert "close matches: test_roti" in text


async def test_compute_meal_bad_unit_names_item_and_units(client: Client) -> None:
    items = [{"food_id": "toor_dal", "amount": 1, "unit": "katori"}]
    text = error_text(await client.call_tool("compute_meal", {"items": items}))
    assert "item 1 (toor_dal)" in text
    assert "valid units: g" in text


async def test_compute_meal_item_count_is_bounded(client: Client) -> None:
    item = {"food_id": "water", "amount": 1, "unit": "g"}
    assert (await client.call_tool("compute_meal", {"items": []})).is_error
    assert (await client.call_tool("compute_meal", {"items": [item] * 31})).is_error


async def test_compute_meal_zero_amount_is_tool_error(client: Client) -> None:
    items = [{"food_id": "test_dal", "amount": 0, "unit": "g"}]
    assert "greater than 0" in error_text(await client.call_tool("compute_meal", {"items": items}))


# find_foods


async def test_find_foods(client: Client) -> None:
    ranked = ok(
        await client.call_tool("find_foods", {"rank_by": "protein_g", "per": "100g", "limit": 2})
    )
    assert ranked["order"] == "desc"
    assert [entry["food"]["id"] for entry in ranked["results"]] == ["chicken_raw", "toor_dal"]
    assert ranked["results"][0]["value"] == 31.0


async def test_find_foods_unknown_nutrient_lists_valid_ones(client: Client) -> None:
    result = await client.call_tool("find_foods", {"rank_by": "magic"})
    assert "protein_g" in error_text(result)


async def test_find_foods_kcal_per_100kcal_is_tool_error(client: Client) -> None:
    result = await client.call_tool("find_foods", {"rank_by": "kcal", "per": "100kcal"})
    assert "per 100 kcal" in error_text(result)


# compute_targets


async def test_compute_targets(client: Client) -> None:
    result = ok(await client.call_tool("compute_targets", PERSON))
    expected = compute_targets(TargetsIn.model_validate(PERSON))
    assert result == expected.model_dump(mode="json")
    assert result["kcal"] == 2556


async def test_compute_targets_rejects_a_minor(client: Client) -> None:
    result = await client.call_tool("compute_targets", {**PERSON, "age": 17})
    assert "age" in error_text(result)


# Sources resource


async def test_sources_resource_lists_licenses(client: Client) -> None:
    listed = (await client.list_resources()).resources
    assert [(str(r.uri), r.mime_type) for r in listed] == [(SOURCES_URI, "application/json")]
    contents = (await client.read_resource(SOURCES_URI)).contents
    block = contents[0]
    assert isinstance(block, TextResourceContents)
    assert block.mime_type == "application/json"
    sources = json.loads(block.text)
    assert {source["id"] for source in sources} == {
        "test_source",
        "test_recipes",
        "test_gi",
        "query_source",
        "query_recipes",
        "query_gi",
    }
    assert {source["license"] for source in sources} == {"CC0-1.0"}
    assert all(source["attribution"] and source["url"] for source in sources)


# Logging


async def test_each_call_logs_one_line_without_inputs(
    client: Client, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.INFO, logger="nutrition_mcp.server")
    await client.call_tool("search_food", {"query": "roti"})
    await client.call_tool("get_nutrition", {"food_id": "secret_food", "amount": 1, "unit": "g"})
    await client.call_tool("get_nutrition", {"food_id": "test_dal", "amount": 0, "unit": "g"})
    water = {"food_id": "water", "amount": 5, "unit": "g"}
    await client.call_tool("compute_meal", {"items": [water]})
    await client.call_tool("compute_targets", PERSON)

    calls = [r for r in caplog.records if r.name == "nutrition_mcp.server"]
    assert [(r.tool, r.outcome, r.count) for r in calls] == [  # type: ignore[attr-defined]
        ("search_food", "ok", 1),
        ("get_nutrition", "error", 0),
        ("get_nutrition", "error", 0),
        ("compute_meal", "ok", 1),
        ("compute_targets", "ok", 1),
    ]
    assert all(r.latency_ms >= 0 for r in calls)  # type: ignore[attr-defined]
    assert not any("secret_food" in r.getMessage() for r in calls)
    # stdout carries the protocol, so a tool never prints.
    assert capsys.readouterr().out == ""


async def test_an_unknown_tool_name_is_not_logged(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nutrition_mcp.server")
    assert (await client.call_tool("secret_tool", {})).is_error
    calls = [r for r in caplog.records if r.name == "nutrition_mcp.server"]
    assert [(r.tool, r.outcome) for r in calls] == [("unknown", "error")]  # type: ignore[attr-defined]


@pytest.mark.usefixtures("restore_logging")
async def test_a_crash_is_logged_with_its_cause_but_no_message(
    client: Client, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Built at run time: the stack prints source lines, and the value must come only from
    # the exception, which the log leaves out.
    detail = "".join(["sec", "ret"])

    def broken_search(*args: Any) -> None:
        raise RuntimeError(detail)

    monkeypatch.setattr(server_module, "search_foods", broken_search)
    configure_logging("INFO")
    result = await client.call_tool("search_food", {"query": "roti"})

    assert error_text(result) == "Error executing tool search_food"
    err = capsys.readouterr().err
    assert detail not in err
    crash = next(json.loads(line) for line in err.splitlines() if '"exceptions"' in line)
    chain = crash["exceptions"]
    # The SDK wraps the original error; the log keeps both, original first.
    assert [link["type"] for link in chain] == ["RuntimeError", "UnexpectedToolError"]
    assert "nutrition_mcp/server.py" in chain[0]["stack"]
    assert "broken_search" in chain[0]["stack"]


async def test_call_tool_passes_on_arguments_a_newer_sdk_adds(
    reg: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def sdk_call_tool(
        self: MCPServer[Any], name: str, arguments: dict[str, Any], *args: Any, **kwargs: Any
    ) -> CallToolResult:
        seen.update(name=name, arguments=arguments, args=args, kwargs=kwargs)
        return CallToolResult(content=[], structured_content={"results": [1, 2]})

    monkeypatch.setattr(MCPServer, "call_tool", sdk_call_tool)
    result = await build_server(reg, SETTINGS).call_tool("search_food", {}, None, new_option=1)
    assert isinstance(result, CallToolResult)
    assert seen == {
        "name": "search_food",
        "arguments": {},
        "args": (None,),
        "kwargs": {"new_option": 1},
    }


@pytest.mark.parametrize(
    ("name", "structured", "expected"),
    [
        ("search_food", {"results": [{}, {}]}, 2),
        ("compute_meal", {"items": [{}]}, 1),
        ("get_nutrition", {"food": {}}, 1),
        ("search_food", None, 0),
        ("search_food", {}, 0),
        ("search_food", {"results": "not a list"}, 0),
        ("compute_meal", {"items": None}, 0),
    ],
)
def test_count_never_raises(name: str, structured: dict[str, Any] | None, expected: int) -> None:
    # A miscount must not turn a good result into a failed call.
    assert _count(name, CallToolResult(content=[], structured_content=structured)) == expected
    assert _count(name, object()) == 0


async def test_stdio_server_logs_json_lines_without_inputs(tmp_path: Path) -> None:
    # The real entry point over stdio, with the bundled data and DEBUG logging switched on.
    stderr_path = tmp_path / "stderr.log"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", RUN_MAIN],
        env={"NUTRITION_MCP_LOG_LEVEL": "DEBUG"},
    )
    with stderr_path.open("w", encoding="utf-8") as errlog:
        async with Client(stdio_client(params, errlog=errlog)) as client:
            assert len((await client.list_tools()).tools) == 5
            await client.call_tool("search_food", {"query": "secretquery"})
            await client.call_tool(
                "get_nutrition", {"food_id": "secret_food", "amount": 1, "unit": "g"}
            )
            await client.call_tool(
                "compute_targets", {**PERSON, "height_cm": 171.3, "weight_kg": 66.6}
            )

    lines = stderr_path.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]
    calls = [entry for entry in entries if "tool" in entry]
    assert [(entry["tool"], entry["outcome"]) for entry in calls] == [
        ("search_food", "ok"),
        ("get_nutrition", "error"),
        ("compute_targets", "ok"),
    ]
    assert {"ts", "level", "msg", "latency_ms", "count"} <= calls[0].keys()
    logged = "\n".join(lines)
    for secret in ("secretquery", "secret_food", "171.3", "66.6"):
        assert secret not in logged


# main()


def run_main_expecting_exit_1(**env: str) -> dict[str, Any]:
    """Run the entry point, check it exits 1 with nothing on stdout, and return its log line."""
    run = subprocess.run(
        [sys.executable, "-c", RUN_MAIN],
        env={**os.environ, **env},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert run.returncode == 1
    assert run.stdout == ""
    lines = run.stderr.splitlines()
    assert len(lines) == 1, run.stderr
    entry: dict[str, Any] = json.loads(lines[0])
    assert entry["level"] == "ERROR"
    return entry


def test_main_exits_on_bad_data(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    # A source with no name, URL, license or the rest.
    (broken / "sources.yaml").write_text("- id: half_a_source\n", encoding="utf-8")
    entry = run_main_expecting_exit_1(NUTRITION_MCP_DATA_DIRS=str(broken))
    assert str(broken / "sources.yaml") in entry["msg"]


def test_main_exits_on_a_bad_setting() -> None:
    entry = run_main_expecting_exit_1(NUTRITION_MCP_LOG_LEVEL="LOUD")
    assert "NUTRITION_MCP_LOG_LEVEL" in entry["msg"]
