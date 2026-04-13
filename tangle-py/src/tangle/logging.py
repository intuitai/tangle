# src/tangle/logging.py

"""Logging configuration for Tangle.

Bridges structlog to OpenTelemetry log export via Python's stdlib logging.
When OTel is enabled, all structured log records are exported over OTLP gRPC
in addition to being written to the console.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

_logger_provider: Any = None
_configured = False


def configure_logging(
    *,
    otel_enabled: bool = False,
    otel_endpoint: str = "http://localhost:4317",
    service_name: str = "tangle",
    log_level: int = logging.INFO,
) -> None:
    """Configure Tangle logging to emit through OpenTelemetry.

    Sets up structlog to route all log records through Python's stdlib
    ``logging``, then attaches an OTel ``LoggingHandler`` that exports
    records via OTLP gRPC when *otel_enabled* is ``True``.

    Safe to call multiple times; subsequent calls reconfigure handlers.

    Args:
        otel_enabled: When True, logs are exported via OTLP in addition
            to console output.
        otel_endpoint: OTLP gRPC endpoint (e.g. ``http://localhost:4317``).
        service_name: Value of the ``service.name`` OTel resource attribute.
        log_level: Minimum log level to emit.
    """
    global _configured

    # Configure structlog to use stdlib logging as the output backend.
    # structlog.get_logger() returns a lazy proxy, so this works even if
    # loggers were created at module-level before this call.
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Set up the "tangle" logger hierarchy.  Child loggers (tangle.otel,
    # tangle.resolver.chain, etc.) inherit handlers automatically.
    root_logger = logging.getLogger("tangle")
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    # Console handler with structlog formatting
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(),
            ],
        )
    )
    root_logger.addHandler(console_handler)

    if otel_enabled:
        _attach_otel_handler(root_logger, otel_endpoint, service_name, log_level)

    _configured = True


def _attach_otel_handler(
    logger: logging.Logger,
    endpoint: str,
    service_name: str,
    log_level: int,
) -> None:
    """Attach an OTel LoggingHandler that exports log records via OTLP."""
    global _logger_provider

    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
    except ImportError:
        import warnings

        warnings.warn(
            "OpenTelemetry packages not installed. "
            "Install tangle-detect[otel] to enable OTel log export.",
            stacklevel=3,
        )
        return

    # Shut down any previously-configured provider before replacing it.
    if _logger_provider is not None:
        _logger_provider.shutdown()

    resource = Resource.create({"service.name": service_name})
    _logger_provider = LoggerProvider(resource=resource)

    exporter = OTLPLogExporter(endpoint=endpoint, insecure=True)
    _logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    set_logger_provider(_logger_provider)

    otel_handler = LoggingHandler(level=log_level, logger_provider=_logger_provider)
    logger.addHandler(otel_handler)


def shutdown_logging() -> None:
    """Flush pending log records and shut down the OTel log provider."""
    global _logger_provider
    if _logger_provider is not None:
        _logger_provider.shutdown()
        _logger_provider = None
