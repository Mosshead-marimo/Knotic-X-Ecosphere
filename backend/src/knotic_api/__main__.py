"""Backend process entry point with mandatory startup validation."""

import sys

from pydantic import ValidationError

from knotic_config import SecretResolutionError, configuration_error_summary

from .app import create_app
from .config import load_backend_settings


def main() -> None:
    """Validate configuration before later tasks start the Flask server."""
    try:
        settings = load_backend_settings()
    except (SecretResolutionError, ValidationError) as error:
        print(configuration_error_summary(error), file=sys.stderr)
        raise SystemExit(78) from None
    create_app(settings).run(host=settings.host, port=settings.port, debug=False)


if __name__ == "__main__":
    main()
