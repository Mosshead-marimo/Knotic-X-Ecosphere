"""Flask application factory and dependency-aware health checks."""

from __future__ import annotations

import hmac
from urllib.request import urlopen

import psycopg
import redis
from flask import Flask, Response, jsonify, request
from flask.typing import ResponseReturnValue

from knotic_config import RuntimeEnvironment

from .auth_api import AuthDependencies, register_oidc_api
from .config import BackendSettings, load_backend_settings
from .console_api import ConsoleDependencies, register_console_api
from .dev_auth_api import DevAuthDependencies, register_dev_auth_api
from .event_stream import EventStreamDependencies, register_event_stream_api
from .lifecycle_api import LifecycleDependencies, build_lifecycle_dependencies, register_lifecycle_api
from .observability import StateDataObservability
from .privacy_logging import install_sensitive_data_filter
from .voice.event_sync import PostgresVoiceEventStore, VoiceEventSynchronizer
from .voice.managed_agent import ManagedAgentConfiguration, ManagedAgentService
from .voice.privacy import PostgresVoiceConsentStore, VoicePrivacyService
from .voice.recovery import PostgresRecoveryStore, VoiceRecoveryCoordinator
from .voice.session_service import AgoraSessionTokenService, LoggingAgoraAuditSink, RedisAgoraSessionStore
from .voice.telemetry import VoiceObservability
from .voice_api import VoiceDependencies, register_voice_api


def create_app(
    settings: BackendSettings | None = None,
    *,
    lifecycle_dependencies: LifecycleDependencies | None = None,
    observability: StateDataObservability | None = None,
) -> Flask:
    resolved = settings or load_backend_settings()
    app = Flask(__name__)
    install_sensitive_data_filter(app.logger)
    app.config["KNOTIC_SETTINGS"] = resolved
    telemetry = observability or StateDataObservability(
        service_name=resolved.service_name,
        environment=resolved.environment.value,
        otlp_endpoint=resolved.otel_exporter_otlp_endpoint,
    )
    app.extensions["knotic_observability"] = telemetry
    dependencies = lifecycle_dependencies or build_lifecycle_dependencies(
        database_url=resolved.database_url.get_secret_value(),
        redis_url=resolved.redis_url.get_secret_value(),
        environment=resolved.environment.value,
        security_key=resolved.session_security_key.get_secret_value().encode(),
        allowed_origins=resolved.allowed_origins,
    )
    register_lifecycle_api(app, dependencies)

    auth_redis = redis.Redis.from_url(
        resolved.redis_url.get_secret_value(),
        decode_responses=False,
        socket_connect_timeout=1,
        socket_timeout=5,
        health_check_interval=30,
    )
    register_oidc_api(
        app,
        AuthDependencies(
            settings=resolved,
            engine=dependencies.engine,
            redis_client=auth_redis,
            browser_sessions=dependencies.browser_sessions,
        ),
    )
    register_console_api(
        app,
        ConsoleDependencies(
            settings=resolved,
            engine=dependencies.engine,
            redis_client=auth_redis,
            browser_sessions=dependencies.browser_sessions,
            rate_limiter=dependencies.rate_limiter,
            allowed_origins=dependencies.allowed_origins,
        ),
    )
    register_event_stream_api(
        app,
        EventStreamDependencies(
            engine=dependencies.engine,
            redis_client=auth_redis,
            browser_sessions=dependencies.browser_sessions,
            environment=resolved.environment.value,
        ),
    )

    if resolved.environment in {RuntimeEnvironment.DEVELOPMENT, RuntimeEnvironment.TEST}:
        register_dev_auth_api(
            app,
            DevAuthDependencies(
                engine=dependencies.engine,
                browser_sessions=dependencies.browser_sessions,
                allowed_origins=dependencies.allowed_origins,
                cookie_secure=False,
            ),
        )

    voice_telemetry = VoiceObservability(
        service_name=resolved.service_name,
        environment=resolved.environment.value,
        otlp_endpoint=resolved.otel_exporter_otlp_endpoint,
    )
    app.extensions["knotic_voice_observability"] = voice_telemetry

    voice_redis = redis.Redis.from_url(
        resolved.redis_url.get_secret_value(),
        decode_responses=False,
        socket_connect_timeout=1,
        socket_timeout=1,
        health_check_interval=30,
    )
    voice_dependencies = VoiceDependencies(
        active_states=dependencies.active_states,
        browser_sessions=dependencies.browser_sessions,
        rate_limiter=dependencies.rate_limiter,
        token_service=AgoraSessionTokenService(
            app_id=resolved.agora_app_id,
            app_certificate=resolved.agora_app_certificate.get_secret_value(),
            store=RedisAgoraSessionStore(
                voice_redis,
                environment=resolved.environment.value,
                master_key=resolved.session_security_key.get_secret_value().encode(),
            ),
            audit_sink=LoggingAgoraAuditSink(app.logger),
        ),
        agora_app_id=resolved.agora_app_id,
        allowed_origins=dependencies.allowed_origins,
        event_synchronizer=VoiceEventSynchronizer(PostgresVoiceEventStore(dependencies.engine)),
        privacy=VoicePrivacyService(
            PostgresVoiceConsentStore(dependencies.engine),
            policy_version=resolved.voice_policy_version,
            allowed_media_regions=frozenset(resolved.voice_media_regions),
        ),
        telemetry=voice_telemetry,
        managed_agents=ManagedAgentService(
            ManagedAgentConfiguration(
                app_id=resolved.agora_app_id,
                app_certificate=resolved.agora_app_certificate.get_secret_value(),
                customer_id=resolved.agora_customer_id.get_secret_value() if resolved.agora_customer_id else None,
                customer_secret=(
                    resolved.agora_customer_secret.get_secret_value() if resolved.agora_customer_secret else None
                ),
                openai_api_key=resolved.openai_api_key.get_secret_value() if resolved.openai_api_key else None,
                llm_url=resolved.agora_llm_url,
                llm_api_key=resolved.agora_llm_api_key.get_secret_value() if resolved.agora_llm_api_key else None,
                api_base_url=resolved.agora_agent_api_url,
            ),
            voice_redis,
            environment=resolved.environment.value,
        ),
    )
    register_voice_api(app, voice_dependencies)
    app.extensions["knotic_voice_recovery"] = VoiceRecoveryCoordinator(PostgresRecoveryStore(dependencies.engine))

    @app.get("/api/v1/health/live")
    def live() -> ResponseReturnValue:
        return jsonify(status="ok")

    @app.get("/api/v1/health/ready")
    def ready() -> ResponseReturnValue:
        checks: dict[str, str] = {}
        try:
            with psycopg.connect(resolved.database_url.get_secret_value(), connect_timeout=2) as connection:
                connection.execute("select 1")
            checks["postgres"] = "ok"
            redis.Redis.from_url(resolved.redis_url.get_secret_value(), socket_connect_timeout=2).ping()
            checks["redis"] = "ok"
            with urlopen(f"{resolved.mcp_base_url}/health/ready", timeout=2) as response:  # noqa: S310 - configured/validated service URL
                if response.status != 200:
                    raise RuntimeError("MCP is not ready")
            checks["mcp"] = "ok"
        except Exception:
            return jsonify(status="not_ready", checks=checks), 503
        return jsonify(status="ok", checks=checks)

    @app.get("/internal/metrics")
    def metrics() -> ResponseReturnValue:
        configured = resolved.metrics_auth_token
        if configured is None:
            return jsonify(error="not_found"), 404
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not supplied or not hmac.compare_digest(supplied, configured.get_secret_value()):
            return jsonify(error="unauthorized"), 401
        telemetry.sample_pool(dependencies.engine)
        payload, content_type = telemetry.render()
        voice_payload, _ = voice_telemetry.render()
        return Response(payload + voice_payload, content_type=content_type)

    return app
