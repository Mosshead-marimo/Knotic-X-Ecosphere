"""Typed Flask service configuration and startup validation."""

from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator

from knotic_config import CommonSettings, RuntimeEnvironment, SecretProvider, load_settings

_REQUIRED_SECRETS = {
    "database_url": "KNOTIC_DATABASE_URL",
    "redis_url": "KNOTIC_REDIS_URL",
    "mcp_auth_token": "KNOTIC_MCP_AUTH_TOKEN",
    "agora_app_certificate": "KNOTIC_AGORA_APP_CERTIFICATE",
    "session_security_key": "KNOTIC_SESSION_SECURITY_KEY",
}
_OPTIONAL_SECRETS = {
    "previous_mcp_auth_token": "KNOTIC_MCP_AUTH_TOKEN_PREVIOUS",
    "metrics_auth_token": "KNOTIC_METRICS_AUTH_TOKEN",
    "openai_api_key": "KNOTIC_OPENAI_API_KEY",
}
_PLACEHOLDERS = ("change-me", "replace-me", "example-only", "insert-secret")


def _is_local_hostname(hostname: str | None) -> bool:
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


class BackendSettings(CommonSettings):
    """Validated settings required before the Flask process can start."""

    service_name: Literal["knotic-api"] = "knotic-api"
    host: str = Field(default="127.0.0.1", validation_alias="KNOTIC_BACKEND_HOST")
    port: int = Field(default=8080, ge=1, le=65535, validation_alias="KNOTIC_BACKEND_PORT")
    allowed_origins: list[str] = Field(default_factory=list, validation_alias="KNOTIC_ALLOWED_ORIGINS")
    mcp_base_url: str = Field(validation_alias="KNOTIC_MCP_BASE_URL")
    agora_app_id: str = Field(validation_alias="KNOTIC_AGORA_APP_ID")
    database_url: SecretStr
    redis_url: SecretStr
    mcp_auth_token: SecretStr
    previous_mcp_auth_token: SecretStr | None = None
    agora_app_certificate: SecretStr
    session_security_key: SecretStr
    metrics_auth_token: SecretStr | None = None
    otel_exporter_otlp_endpoint: str | None = Field(default=None, validation_alias="KNOTIC_OTEL_EXPORTER_OTLP_ENDPOINT")
    openai_api_key: SecretStr | None = None
    openai_model: Literal["gpt-5.6-terra"] = Field(default="gpt-5.6-terra", validation_alias="KNOTIC_OPENAI_MODEL")
    openai_timeout_seconds: float = Field(
        default=12.0, ge=1.0, le=30.0, validation_alias="KNOTIC_OPENAI_TIMEOUT_SECONDS"
    )

    @field_validator("mcp_base_url")
    @classmethod
    def validate_mcp_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("KNOTIC_MCP_BASE_URL must be an absolute HTTP(S) URL")
        return value.rstrip("/")

    @field_validator("agora_app_id")
    @classmethod
    def validate_agora_app_id(cls, value: str) -> str:
        if len(value.strip()) < 16 or any(placeholder in value.casefold() for placeholder in _PLACEHOLDERS):
            raise ValueError("KNOTIC_AGORA_APP_ID must be a non-placeholder provider identifier")
        return value.strip()

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

    @field_validator("mcp_auth_token", "previous_mcp_auth_token")
    @classmethod
    def validate_mcp_token(cls, value: SecretStr | None) -> SecretStr | None:
        return _validate_secret("MCP authentication token", value, 32) if value is not None else None

    @field_validator("agora_app_certificate")
    @classmethod
    def validate_agora_certificate(cls, value: SecretStr) -> SecretStr:
        return _validate_secret("KNOTIC_AGORA_APP_CERTIFICATE", value, 32)

    @field_validator("session_security_key")
    @classmethod
    def validate_session_security_key(cls, value: SecretStr) -> SecretStr:
        return _validate_secret("KNOTIC_SESSION_SECURITY_KEY", value, 32)

    @field_validator("metrics_auth_token")
    @classmethod
    def validate_metrics_auth_token(cls, value: SecretStr | None) -> SecretStr | None:
        return _validate_secret("KNOTIC_METRICS_AUTH_TOKEN", value, 32) if value is not None else None

    @field_validator("openai_api_key")
    @classmethod
    def validate_openai_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        return _validate_secret("KNOTIC_OPENAI_API_KEY", value, 20) if value is not None else None

    @field_validator("otel_exporter_otlp_endpoint")
    @classmethod
    def validate_otel_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("KNOTIC_OTEL_EXPORTER_OTLP_ENDPOINT must be an absolute HTTP(S) URL")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_managed_environment(self) -> "BackendSettings":
        if self.environment in {RuntimeEnvironment.STAGING, RuntimeEnvironment.PRODUCTION}:
            if not self.allowed_origins:
                raise ValueError("KNOTIC_ALLOWED_ORIGINS is required in staging and production")
            invalid = [
                origin
                for origin in self.allowed_origins
                if origin == "*" or not origin.startswith("https://") or _is_local_hostname(urlparse(origin).hostname)
            ]
            if invalid:
                raise ValueError("managed-environment origins must be explicit HTTPS origins")
            if self.metrics_auth_token is None:
                raise ValueError("KNOTIC_METRICS_AUTH_TOKEN is required in staging and production")
            if self.otel_exporter_otlp_endpoint is None:
                raise ValueError("KNOTIC_OTEL_EXPORTER_OTLP_ENDPOINT is required in staging and production")
        return self


def load_backend_settings(
    *,
    secret_provider: SecretProvider | None = None,
    env_file: str | Path | None = None,
) -> BackendSettings:
    return load_settings(
        BackendSettings,
        required_secrets=_REQUIRED_SECRETS,
        optional_secrets=_OPTIONAL_SECRETS,
        secret_provider=secret_provider,
        env_file=env_file,
    )
