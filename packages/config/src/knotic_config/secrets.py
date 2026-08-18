"""Secret-provider boundary used by service configuration loaders."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import SecretStr


@runtime_checkable
class SecretProvider(Protocol):
    """Retrieve a secret without exposing storage details to application code."""

    def get_secret(self, name: str) -> str | None:
        """Return the secret value or ``None`` when the secret is unavailable."""


@dataclass(frozen=True, slots=True)
class EnvironmentSecretProvider:
    """Read secrets from the process environment."""

    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    def get_secret(self, name: str) -> str | None:
        value = self.environ.get(name)
        return value if value and value.strip() else None


@dataclass(frozen=True, slots=True)
class MappingSecretProvider:
    """Deterministic provider for tests and injected secret-manager adapters."""

    values: Mapping[str, str]

    def get_secret(self, name: str) -> str | None:
        value = self.values.get(name)
        return value if value and value.strip() else None


class SecretResolutionError(RuntimeError):
    """Raised when required secret names cannot be resolved."""

    def __init__(self, missing_names: list[str]) -> None:
        self.missing_names = tuple(sorted(missing_names))
        super().__init__(f"missing required secrets: {', '.join(self.missing_names)}")


def resolve_secrets(
    provider: SecretProvider,
    required: Mapping[str, str],
    optional: Mapping[str, str] | None = None,
) -> dict[str, SecretStr | None]:
    """Resolve external secret names into typed settings field values."""

    resolved: dict[str, SecretStr | None] = {}
    missing: list[str] = []

    for field_name, secret_name in required.items():
        value = provider.get_secret(secret_name)
        if value is None:
            missing.append(secret_name)
        else:
            resolved[field_name] = SecretStr(value)

    for field_name, secret_name in (optional or {}).items():
        value = provider.get_secret(secret_name)
        resolved[field_name] = SecretStr(value) if value is not None else None

    if missing:
        raise SecretResolutionError(missing)

    return resolved
