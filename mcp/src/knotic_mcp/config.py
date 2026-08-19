"""Typed MCP gateway configuration and startup validation."""

from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator

from knotic_config import CommonSettings, RuntimeEnvironment, SecretProvider, load_settings

_REQUIRED_SECRETS = {
    "database_url": "KNOTIC_DATABASE_URL",
    "redis_url": "KNOTIC_REDIS_URL",
    "auth_token": "KNOTIC_MCP_AUTH_TOKEN",
}
_OPTIONAL_SECRETS = {
    "previous_auth_token": "KNOTIC_MCP_AUTH_TOKEN_PREVIOUS",
}
_PLACEHOLDERS = ("change-me", "replace-me", "example-only", "insert-secret")


def _is_local_host(value: str) -> bool:
    hostname = urlparse(f"//{value}").hostname
    if not hostname:
        return True
    if hostname.casefold() == "localhost" or hostname.casefold().endswith(".localhost"):
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validate_secret(name: str, secret: SecretStr, minimum_length: int = 16) -> SecretStr:
    raw = secret.get_secret_value()
    if len(raw) < minimum_length:
        raise ValueError(f"{name} must contain at least {minimum_length} characters")
    if any(placeholder in raw.casefold() for placeholder in _PLACEHOLDERS):
        raise ValueError(f"{name} must not use an example placeholder")
    return secret


class McpSettings(CommonSettings):
    """Validated settings required before the MCP process can start."""

    service_name: Literal["knotic-mcp"] = "knotic-mcp"
    host: str = Field(default="127.0.0.1", validation_alias="KNOTIC_MCP_HOST")
    port: int = Field(default=8090, ge=1, le=65535, validation_alias="KNOTIC_MCP_PORT")
    allowed_hosts: list[str] = Field(default_factory=list, validation_alias="KNOTIC_MCP_ALLOWED_HOSTS")
    database_url: SecretStr
    redis_url: SecretStr
    auth_token: SecretStr
    previous_auth_token: SecretStr | None = None

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        value = _validate_secret("KNOTIC_DATABASE_URL", value)
        if not value.get_secret_value().startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("KNOTIC_DATABASE_URL must use PostgreSQL")
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr) -> SecretStr:
        value = _validate_secret("KNOTIC_REDIS_URL", value)
        if not value.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("KNOTIC_REDIS_URL must use Redis")
        return value

    @field_validator("auth_token", "previous_auth_token")
    @classmethod
    def validate_auth_token(cls, value: SecretStr | None) -> SecretStr | None:
        return _validate_secret("MCP authentication token", value, 32) if value is not None else None

    @model_validator(mode="after")
    def validate_managed_environment(self) -> "McpSettings":
        if self.environment in {RuntimeEnvironment.STAGING, RuntimeEnvironment.PRODUCTION}:
            if not self.allowed_hosts:
                raise ValueError("KNOTIC_MCP_ALLOWED_HOSTS is required in staging and production")
            invalid = [host for host in self.allowed_hosts if host == "*" or _is_local_host(host)]
            if invalid:
                raise ValueError("managed-environment hosts must be explicit non-local hosts")
        return self


def load_mcp_settings(
    *,
    secret_provider: SecretProvider | None = None,
    env_file: str | Path | None = None,
) -> McpSettings:
    return load_settings(
        McpSettings,
        required_secrets=_REQUIRED_SECRETS,
        optional_secrets=_OPTIONAL_SECRETS,
        secret_provider=secret_provider,
        env_file=env_file,
    )
