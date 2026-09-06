"""Console event relay process entry point."""

import redis

from knotic_api.config import load_backend_settings
from knotic_api.event_stream import DomainEventRelay, EventStreamDependencies
from knotic_api.lifecycle_api import build_lifecycle_dependencies


def main() -> None:
    settings = load_backend_settings()
    lifecycle = build_lifecycle_dependencies(
        database_url=settings.database_url.get_secret_value(),
        redis_url=settings.redis_url.get_secret_value(),
        environment=settings.environment.value,
        security_key=settings.session_security_key.get_secret_value().encode(),
        allowed_origins=settings.allowed_origins,
    )
    client = redis.Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=False, socket_timeout=12)
    relay = DomainEventRelay(
        EventStreamDependencies(lifecycle.engine, client, lifecycle.browser_sessions, settings.environment.value)
    )
    relay.run_forever()


if __name__ == "__main__":
    main()
