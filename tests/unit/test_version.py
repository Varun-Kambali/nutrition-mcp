import re

import nutrition_mcp


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", nutrition_mcp.__version__)
