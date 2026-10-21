"""The built Docker image, driven over stdio like an MCP client starts it.

CI builds the image and runs this file with NUTRITION_MCP_IMAGE set to its tag. Everywhere
else the tests skip, so `make test` needs no Docker.
"""

import json
import os
import subprocess

import pytest
from mcp import Client, StdioServerParameters
from mcp.types import TextResourceContents

from nutrition_mcp.server import SOURCES_URI, TOOL_NAMES

IMAGE = os.environ.get("NUTRITION_MCP_IMAGE", "")

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not IMAGE, reason="set NUTRITION_MCP_IMAGE to a built image tag"),
]

# Starting a container takes longer than starting the server from a venv.
TIMEOUT_S = 60.0


def container() -> StdioServerParameters:
    # -i keeps stdin open for the protocol; no -t, since a terminal would mangle it.
    return StdioServerParameters(command="docker", args=["run", "-i", "--rm", IMAGE])


async def test_the_image_serves_the_tools_and_the_bundled_data() -> None:
    async with Client(container(), read_timeout_seconds=TIMEOUT_S) as client:
        assert {tool.name for tool in (await client.list_tools()).tools} == set(TOOL_NAMES)

        found = await client.call_tool("search_food", {"query": "roti"})
        assert not found.is_error
        assert found.structured_content is not None
        assert found.structured_content["results"][0]["id"] == "chapati"

        meal = await client.call_tool(
            "compute_meal", {"items": [{"food_id": "chapati", "amount": 2, "unit": "roti"}]}
        )
        assert not meal.is_error
        assert meal.structured_content is not None
        assert meal.structured_content["total"]["kcal"] > 0

        [contents] = (await client.read_resource(SOURCES_URI)).contents
        assert isinstance(contents, TextResourceContents)
        assert {source["id"] for source in json.loads(contents.text)} == {
            "usda_fdc",
            "cofid_2021",
            "recipes_cc_by",
            "gi_compendium_2021",
        }


def test_the_image_runs_as_a_user_without_root() -> None:
    uid = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "id", IMAGE, "-u"],
        capture_output=True,
        text=True,
        check=True,
        timeout=TIMEOUT_S,
    ).stdout.strip()
    assert uid == "1000"
