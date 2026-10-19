import os
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Server settings, read from NUTRITION_MCP_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="NUTRITION_MCP_")

    # NoDecode stops pydantic-settings from parsing the variable as JSON, because the value is
    # a plain path list such as "/a:/b".
    data_dirs: Annotated[list[Path], NoDecode] = []
    default_katori_g: float = 150.0
    log_level: str = "INFO"

    @field_validator("data_dirs", mode="before")
    @classmethod
    def _split_data_dirs(cls, value: object) -> object:
        if isinstance(value, str):
            return [Path(part) for part in value.split(os.pathsep) if part]
        return value
