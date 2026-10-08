"""Startup validation and protection against source-mode mixing."""
from dataclasses import replace
import pytest
from sqlalchemy import create_engine, select, func

from vedrfolnir.config import settings
from vedrfolnir.db import HistoricalRun, create_session_factory
from vedrfolnir.service import EngineService


def test_invalid_source_is_rejected():
    with pytest.raises(ValueError, match="SOURCE_MODE"):
        replace(settings, mode="not-a-source").validate()


def test_nonpositive_poller_interval_is_rejected():
    with pytest.raises(ValueError, match="poll_seconds"):
        replace(settings, poll_seconds=0).validate()


def test_source_mode_cannot_change_on_existing_storage(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path/'state.sqlite'}")
    factory = create_session_factory(engine)
    EngineService(factory, replace(settings, mode="demo"))
    with pytest.raises(RuntimeError, match="fresh SQLite"):
        EngineService(factory, replace(settings, mode="mssql"))
    with factory() as session:
        assert session.scalar(select(func.count(HistoricalRun.run_id))) == 0
    engine.dispose()


@pytest.mark.parametrize("token", [
    "CHANGE_ME_LONG_RANDOM_TOKEN", "change_me", "REPLACE_ME", " YOUR_TOKEN ",
])
def test_example_admin_credentials_are_rejected(token):
    with pytest.raises(ValueError, match="ETA_ADMIN_TOKEN"):
        replace(settings, admin_token=token).validate()


def test_custom_admin_token_is_allowed():
    replace(settings, admin_token="a-unique-development-secret").validate()
