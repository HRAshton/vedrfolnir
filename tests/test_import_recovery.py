"""Cursor contract failures and interrupted-page recovery without data loss."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select

from vedrfolnir.config import settings
from vedrfolnir.db import HistoricalRun, SyncState, create_session_factory, make_engine
from vedrfolnir.features import timestamp
from vedrfolnir.service import EngineService
from vedrfolnir.source_validation import validate_source

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
END = NOW-timedelta(hours=1)


def row(name, finish=END):
    return {"run_id": name, "job_id": "compile", "plan_id": "p", "status": "passed",
            "started_at": finish-timedelta(minutes=10), "finished_at": finish}


class Source:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def historical(self, at, identifier, limit):
        cursor = (timestamp(at), identifier)
        self.calls.append(cursor)
        return [r for r in self.rows if (timestamp(r["finished_at"]), r["run_id"]) > cursor][:limit]

    def live(self, since):
        return [], []


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr("vedrfolnir.service.utcnow", lambda: NOW)
    engine = make_engine(f"sqlite:///{tmp_path / 'state.sqlite'}")
    factory = create_session_factory(engine)
    config = replace(settings, mode="files", import_batch=2, import_limit_per_poll=20)
    svc = EngineService(factory, config)
    yield svc, factory, engine
    engine.dispose()


@pytest.mark.parametrize("rows,code", [
    ([row("2"), row("1")], "historical_cursor_order_invalid"),
    ([row("2", END+timedelta(minutes=1)), row("3")], "historical_cursor_order_invalid"),
    ([row("1"), row("2"), row("3")], "historical_batch_limit_exceeded"),
    ([row("1", NOW+timedelta(minutes=1))], "historical_finish_in_future"),
])
def test_bad_page_is_rejected_before_any_rows_or_cursor_are_committed(state, rows, code):
    svc, factory, _ = state
    class Broken(Source):
        def historical(self, *args):
            return rows
    with pytest.raises(ValueError, match=code):
        svc.import_history(Broken(rows))
    with factory() as s:
        assert s.scalar(select(func.count(HistoricalRun.run_id))) == 0
        assert s.get(SyncState, "history_cursor") is None


def test_preflight_reports_cursor_contract_errors_without_echoing_ids(state):
    class Broken(Source):
        def historical(self, *args):
            return [row("private-z"), row("private-a")]
    report = validate_source(Broken([]))
    assert not report["ok"]
    assert report["datasets"]["historical_jobs"]["errors"] == [
        {"row": 2, "code": "historical_cursor_order_invalid"}]
    assert "private-" not in str(report)


def test_failed_second_page_resumes_after_committed_page_across_restart(state):
    svc, factory, engine = state
    rows = [row(str(i)) for i in range(1, 6)]
    class Interrupted(Source):
        def historical(self, at, identifier, limit):
            if identifier == "2":
                raise ConnectionError("temporary_source_failure")
            return super().historical(at, identifier, limit)
    with pytest.raises(ConnectionError):
        svc.import_history(Interrupted(rows))
    with factory() as s:
        assert set(s.scalars(select(HistoricalRun.run_id))) == {"1", "2"}
        cursor = s.get(SyncState, "history_cursor").value
        assert cursor.endswith("|2")
    engine.dispose()
    resumed = EngineService(factory, svc.config)
    source = Source(rows)
    assert resumed.import_history(source) == 3
    assert source.calls[0] == (END, "2")
    assert resumed.import_history(source) == 0
    with factory() as s:
        assert set(s.scalars(select(HistoricalRun.run_id))) == {"1", "2", "3", "4", "5"}
        assert s.get(SyncState, "history_cursor").value.endswith("|5")


@pytest.mark.parametrize("point", ["history_insert", "cursor_write"])
def test_interrupted_page_transaction_rolls_back_history_and_cursor_together(state, point):
    svc, factory, engine = state
    source = Source([row("1"), row("2"), row("3")])
    def fail(connection, cursor, statement, parameters, context, many):
        if ((point == "history_insert" and statement.startswith("INSERT INTO historical_runs"))
                or (point == "cursor_write" and "sync_state" in statement
                    and statement.startswith(("INSERT", "UPDATE")) and "history_cursor" in parameters)):
            raise RuntimeError("interrupted_page_transaction")
    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="interrupted_page_transaction"):
            svc.import_history(source)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    with factory() as s:
        assert s.scalar(select(func.count(HistoricalRun.run_id))) == 0
        assert s.get(SyncState, "history_cursor") is None
    assert svc.import_history(source) == 3
    with factory() as s:
        assert s.scalar(select(func.count(HistoricalRun.run_id))) == 3


def test_replayed_old_key_cannot_advance_a_page_with_a_new_last_key(state):
    svc, factory, _ = state
    svc.import_history(Source([row("1"), row("2")]))
    class Replayed(Source):
        def historical(self, *args):
            return [row("1"), row("3")]
    with pytest.raises(ValueError, match="historical_cursor_order_invalid"):
        svc.import_history(Replayed([]))
    with factory() as s:
        assert set(s.scalars(select(HistoricalRun.run_id))) == {"1", "2"}
        assert s.get(SyncState, "history_cursor").value.endswith("|2")


def test_tied_completion_ids_use_lexical_order_and_poll_budget_can_resume(state):
    svc, factory, _ = state
    limited = EngineService(factory, replace(svc.config, import_limit_per_poll=2))
    source = Source([row("1"), row("10"), row("2"), row("3")])
    assert limited.import_history(source) == 2
    with factory() as s:
        assert s.get(SyncState, "history_cursor").value.endswith("|10")
    assert limited.import_history(source) == 2
    assert limited.import_history(source) == 0
    with factory() as s:
        assert set(s.scalars(select(HistoricalRun.run_id))) == {"1", "10", "2", "3"}


def test_future_history_page_cannot_jump_the_cursor_past_later_valid_completion(state):
    svc, factory, _ = state
    source = Source([row("future", NOW+timedelta(days=1))])
    with pytest.raises(ValueError, match="historical_finish_in_future"):
        svc.import_history(source)
    source.rows = [row("valid", NOW-timedelta(minutes=1))]
    assert svc.import_history(source) == 1
    with factory() as s:
        assert s.get(HistoricalRun, "future") is None
        assert s.get(HistoricalRun, "valid") is not None


def test_abrupt_process_exit_during_cursor_write_recovers_uncommitted_wal(state):
    import subprocess
    import sys
    svc, factory, engine = state
    # The subprocess exits without closing connections or rolling back Python
    # contexts, after the row INSERT and before its cursor commits.
    code = f'''
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from sqlalchemy import event
from vedrfolnir.config import settings
from vedrfolnir.db import make_engine, create_session_factory
from vedrfolnir.service import EngineService
engine = make_engine({str(engine.url)!r})
factory = create_session_factory(engine)
service = EngineService(factory, replace(settings, mode="files"))
end = datetime(2026, 10, 13, 11, tzinfo=timezone.utc)
class Source:
    def historical(self, *args):
        return [{{"run_id": "crash-run", "job_id": "compile", "status": "passed",
                 "started_at": end-timedelta(minutes=10), "finished_at": end}}]
def crash(conn, cursor, statement, parameters, context, many):
    if "sync_state" in statement and statement.startswith(("INSERT", "UPDATE")) and "history_cursor" in parameters:
        os._exit(17)
event.listen(engine, "before_cursor_execute", crash)
service.import_history(Source())
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 17, result.stderr
    engine.dispose()
    with factory() as s:
        assert s.get(HistoricalRun, "crash-run") is None
        assert s.get(SyncState, "history_cursor") is None
    resumed = EngineService(factory, svc.config)
    assert resumed.import_history(Source([row("crash-run")])) == 1
    with factory() as s:
        assert s.get(HistoricalRun, "crash-run") is not None
