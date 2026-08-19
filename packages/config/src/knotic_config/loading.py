"""Typed settings loading with explicit environment-file and secret sources."""

from collections.abc import Mapping
from pathlib import Path

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings

from .secrets import EnvironmentSecretProvider, SecretProvider, resolve_secrets


def load_settings[SettingsT: BaseSettings](
    settings_type: type[SettingsT],
    *,
    required_secrets: Mapping[str, str],
    optional_secrets: Mapping[str, str] | None = None,
    secret_provider: SecretProvider | None = None,
    env_file: str | Path | None = None,
) -> SettingsT:
    """Load validated settings; files are read only when explicitly supplied."""

    provider = secret_provider or EnvironmentSecretProvider()
    secret_values = resolve_secrets(provider, required_secrets, optional_secrets)
    # BaseSettings' generated initializer cannot express dynamic model field names.
    return settings_type(_env_file=env_file, **secret_values)  # type: ignore[arg-type]


def configuration_error_summary(error: Exception) -> str:
    """Return an actionable configuration error without echoing input values."""

    if isinstance(error, ValidationError):
        details = [f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()]
        return "invalid configuration: " + "; ".join(details)
    return f"invalid configuration: {error}"


def secret_values(settings: BaseSettings) -> tuple[str, ...]:
    """Collect resolved secret values for exact-value log redaction."""

    values: list[str] = []
    for value in settings.__dict__.values():
        if isinstance(value, SecretStr):
            raw = value.get_secret_value()
            if raw:
                values.append(raw)
    return tuple(values)
