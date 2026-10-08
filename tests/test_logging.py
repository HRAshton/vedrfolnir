"""Default logging and user-supplied standard dictConfig handlers."""
import json
import logging
from contextlib import contextmanager

import pytest

from vedrfolnir.logging_setup import JsonLogFormatter, configure_logging


@contextmanager
def preserved_logging():
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    uvicorn = [
        (logging.getLogger(name), logging.getLogger(name).handlers[:],
         logging.getLogger(name).level, logging.getLogger(name).propagate)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
    ]
    try:
        yield
    finally:
        for handler in root.handlers:
            if handler not in old_handlers:
                handler.close()
        root.handlers = old_handlers
        root.setLevel(old_level)
        for logger, handlers, level, propagate in uvicorn:
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate


def test_json_log_contains_only_approved_context():
    record = logging.LogRecord("vedrfolnir.service", logging.INFO, __file__, 1,
                               "Completed %s", ("sync",), None)
    record.event = "sync_complete"
    record.imported = 12
    record.password = "should-not-leak"
    line = json.loads(JsonLogFormatter().format(record))
    assert line["message"] == "Completed sync"
    assert line["imported"] == 12
    assert line["event"] == "sync_complete"
    assert "password" not in line
    assert line["timestamp"].endswith("+00:00")


def test_default_json_console_and_uvicorn_forwarding(capsys):
    with preserved_logging():
        handlers = configure_logging(level="INFO", log_format="json")
        assert len(handlers) == 1
        logging.getLogger("vedrfolnir.test").info("hello", extra={"event": "startup"})
        logging.getLogger("uvicorn.access").info("access logged")
        entries = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
        assert [entry["message"] for entry in entries] == ["hello", "access logged"]


def test_plain_text_console(capsys):
    with preserved_logging():
        configure_logging(level="WARNING", log_format="text")
        logging.getLogger("vedrfolnir.test").info("should not appear")
        logging.getLogger("vedrfolnir.test").warning("plain message")
        result = capsys.readouterr().out
        assert "WARNING vedrfolnir.test plain message" in result
        assert "should not appear" not in result


def test_custom_config_attaches_standard_file_handler(tmp_path, capsys):
    destination = tmp_path / "worker.log"
    config_file = tmp_path / "logging.json"
    config_file.write_text(json.dumps({
        "version": 1,
        "formatters": {"json": {"()": "vedrfolnir.logging_setup.JsonLogFormatter"}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler", "formatter": "json",
                "stream": "ext://sys.stdout"
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler", "formatter": "json",
                "filename": str(destination), "maxBytes": 10000, "backupCount": 2
            }
        },
        "root": {"level": "DEBUG", "handlers": ["console", "file"]}
    }), encoding="utf-8")
    with preserved_logging():
        handlers = configure_logging(config_file=str(config_file))
        assert len(handlers) == 2
        logging.getLogger("vedrfolnir.service").info("custom route", extra={"imported": 42})
        assert json.loads(capsys.readouterr().out.strip())["imported"] == 42
        assert json.loads(destination.read_text().strip())["message"] == "custom route"


def test_custom_logger_keeps_explicit_uvicorn_configuration(tmp_path):
    path = tmp_path / "logging.json"
    path.write_text(json.dumps({
        "version": 1,
        "handlers": {"null": {"class": "logging.NullHandler"}},
        "root": {"level": "INFO", "handlers": ["null"]},
        "loggers": {
            "uvicorn.access": {"level": "WARNING", "handlers": ["null"], "propagate": False}
        }
    }), encoding="utf-8")
    with preserved_logging():
        configure_logging(config_file=str(path))
        logger = logging.getLogger("uvicorn.access")
        assert not logger.propagate
        assert isinstance(logger.handlers[0], logging.NullHandler)


@pytest.mark.parametrize("config", ["not json", "{}", '[]', '{"version": 2}'])
def test_invalid_custom_file_rejected(tmp_path, config):
    path = tmp_path / "logging.json"
    path.write_text(config, encoding="utf-8")
    with pytest.raises(ValueError, match="LOG_CONFIG_FILE"):
        configure_logging(config_file=str(path))


def test_missing_custom_config_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="LOG_CONFIG_FILE"):
        configure_logging(config_file=str(tmp_path / "missing.json"))


@pytest.mark.parametrize("level,log_format", [("nope", "json"), ("INFO", "yaml")])
def test_invalid_default_settings_rejected(level, log_format):
    with pytest.raises(ValueError):
        configure_logging(level=level, log_format=log_format)
