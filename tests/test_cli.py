"""Wheel bootstrap and CLI parsing without running an HTTP server."""
import os

from vedrfolnir import __version__
from vedrfolnir import cli


def test_init_is_not_a_supported_command(capsys):
    import pytest
    with pytest.raises(SystemExit) as exc:
        cli.main(["init"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_version_entrypoint(capsys):
    try:
        cli.main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    assert __version__ in capsys.readouterr().out


def test_doctor_checks_source_files(tmp_path, capsys):
    from dataclasses import replace
    from vedrfolnir.config import settings
    config = replace(settings, mode="files", csv_dir=str(tmp_path / "missing"),
                     admin_token="safe-token")
    assert cli.diagnose(config) == 1
    assert "Missing required CSV" in capsys.readouterr().out


def test_env_file_never_overrides_explicit_process_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ETA_ADMIN_TOKEN=example-file-secret\n", encoding="utf-8")
    monkeypatch.setenv("ETA_ADMIN_TOKEN", "process-secret")
    cli.load_env(env)
    assert os.environ["ETA_ADMIN_TOKEN"] == "process-secret"
