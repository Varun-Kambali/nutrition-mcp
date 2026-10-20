import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from nutrition_mcp import loader


@pytest.fixture
def no_bundled_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Swap the bundled dataset for an empty one.

    build_registry always loads the bundled folder first, so without this a test fixture
    that reuses a bundled ID (toor_dal, ghee) fails as a duplicate, and assertions about
    load order would depend on whatever data ships with the package.
    """
    empty = tmp_path / "empty_bundled"
    (empty / "dishes").mkdir(parents=True)
    (empty / "sources.yaml").write_text("[]\n", encoding="utf-8")
    (empty / "ingredients.csv").write_text(
        ",".join(loader.INGREDIENT_COLUMNS) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(loader, "bundled_dir", lambda: empty)
    return empty


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Put the root and SDK loggers back after a test that calls configure_logging."""
    root = logging.getLogger()
    sdk = logging.getLogger("mcp")
    saved = (root.handlers[:], root.level, sdk.level)
    yield
    root.handlers[:], root.level = saved[0], saved[1]
    sdk.setLevel(saved[2])
