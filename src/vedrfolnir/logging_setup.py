"""Standard-library logging configuration, with structured stdout by default.

A trusted user-supplied dictConfig file can replace the default handlers without
introducing service-specific integrations for log backends.
"""
import json
import logging
from logging.config import dictConfig
from datetime import datetime, timezone
from pathlib import Path

# Explicit allowlist: never serialize SQL parameters, credentials, or arbitrary extras.
CONTEXT_FIELDS = (
    "event", "source_mode", "imported", "trained_snapshots", "new_predictions",
    "elapsed_ms", "poll_seconds", "exception_type", "plan_id",
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None and isinstance(value, (str, int, float, bool)):
                data[field] = value
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def default_log_config(level="INFO", log_format="json"):
    level = level.upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
    if log_format not in ("json", "text"):
        raise ValueError("LOG_FORMAT must be json or text")
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {"()": "vedrfolnir.logging_setup.JsonLogFormatter"},
            "text": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": "DEBUG",
                "formatter": log_format,
                "stream": "ext://sys.stdout",
            },
        },
        "root": {"level": level, "handlers": ["console"]},
    }


def configure_logging(*, level="INFO", log_format="json", config_file=""):
    """Configure standard Python logging from defaults or a trusted JSON file.

    A custom file provides the entire dictConfig, including handlers and levels.
    Custom handlers must be installed in the image and are executable Python code.
    """
    if config_file:
        path = Path(config_file)
        try:
            with path.open("r", encoding="utf-8") as fh:
                config = json.load(fh)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read LOG_CONFIG_FILE {str(path)!r}: {exc}") from exc
        if not isinstance(config, dict) or config.get("version") != 1:
            raise ValueError("LOG_CONFIG_FILE must contain a logging dictConfig object with version 1")
        # Keep library loggers available unless the user explicitly disables them.
        config.setdefault("disable_existing_loggers", False)
    else:
        config = default_log_config(level, log_format)

    dictConfig(config)

    # Uvicorn installs independent console handlers before FastAPI startup. If
    # not explicitly configured by the user, let them inherit the root handlers
    # so stdout has one consistent format and no duplicate access logs.
    configured_loggers = config.get("loggers", {})
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        if name not in configured_loggers:
            logger = logging.getLogger(name)
            logger.handlers.clear()
            logger.propagate = True

    return logging.getLogger().handlers
