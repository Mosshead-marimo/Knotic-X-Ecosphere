"""Shared runtime environment and logging configuration."""

from enum import StrEnum

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class CommonSettings(BaseSettings):
    """Settings shared by every server process."""

    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="ignore",
        env_file=None,
        env_file_encoding="utf-8",
        hide_input_in_errors=True,
    )

    environment: RuntimeEnvironment = Field(
        default=RuntimeEnvironment.DEVELOPMENT,
        validation_alias="KNOTIC_ENV",
    )
    log_level: LogLevel = Field(default=LogLevel.INFO, validation_alias="KNOTIC_LOG_LEVEL")
    debug: bool = Field(default=False, validation_alias="KNOTIC_DEBUG")

    @model_validator(mode="after")
    def reject_debug_in_managed_environments(self) -> "CommonSettings":
        if self.environment in {RuntimeEnvironment.STAGING, RuntimeEnvironment.PRODUCTION} and self.debug:
            raise ValueError("debug mode must be disabled in staging and production")
        return self

