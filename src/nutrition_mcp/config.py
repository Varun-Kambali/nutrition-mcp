import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_PREFIX = "NUTRITION_MCP_"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Server settings, read from NUTRITION_MCP_* environment variables."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX)

    # NoDecode stops pydantic-settings from parsing the variable as JSON, because the value is
    # a plain path list such as "/a:/b".
    data_dirs: Annotated[list[Path], NoDecode] = []
    # Grams in a katori for a dish that defines none. Above 0 so a katori is never 0 g, and at
    # most 1000 g so a typo cannot turn one katori into a whole pot.
    default_katori_g: Annotated[float, Field(gt=0, le=1000)] = 150.0
    log_level: LogLevel = "INFO"

    @field_validator("data_dirs", mode="before")
    @classmethod
    def _split_data_dirs(cls, value: object) -> object:
        if isinstance(value, str):
            return [Path(part) for part in value.split(os.pathsep) if part]
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        # The logging module takes "debug" as readily as "DEBUG", so the variable does too.
        return value.upper() if isinstance(value, str) else value


def describe_settings_error(error: ValidationError) -> str:
    """Name each environment variable that failed and why, without echoing its value."""
    return "; ".join(
        f"{ENV_PREFIX}{'.'.join(str(part) for part in problem['loc']).upper()}: {problem['msg']}"
        for problem in error.errors()
    )
