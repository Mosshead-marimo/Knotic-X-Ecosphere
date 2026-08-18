"""Backend process entry point with mandatory startup validation."""

import sys

from knotic_config import SecretResolutionError, configuration_error_summary
from pydantic import ValidationError

from .config import load_backend_settings


def main() -> None:
    """Validate configuration before later tasks start the Flask server."""
    try:
        load_backend_settings()
    except (SecretResolutionError, ValidationError) as error:
        print(configuration_error_summary(error), file=sys.stderr)
        raise SystemExit(78) from None
    raise SystemExit("Flask service bootstrap is pending subsequent Phase 0 tasks.")


if __name__ == "__main__":
    main()
