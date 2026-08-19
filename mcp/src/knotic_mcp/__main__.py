"""MCP process entry point with mandatory startup validation."""

import sys

import uvicorn
from pydantic import ValidationError

from knotic_config import SecretResolutionError, configuration_error_summary

from .app import create_app
from .config import load_mcp_settings


def main() -> None:
    """Validate configuration before later tasks start the MCP server."""
    try:
        settings = load_mcp_settings()
    except (SecretResolutionError, ValidationError) as error:
        print(configuration_error_summary(error), file=sys.stderr)
        raise SystemExit(78) from None
    uvicorn.run(
        create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level.value.lower()
    )


if __name__ == "__main__":
    main()
