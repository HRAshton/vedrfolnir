"""Late inserted completion keys: overlap, bounded budgets, atomic recovery."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select

from vedrfolnir.config import settings
from vedrfolnir.db import HistoricalRun, SyncState, create_session_factory, make_engine
from vedrfolnir.features import timestamp
from vedrfolnir.service import EngineService

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)


def finished(name, hours):
    end = NOW - timedelta(hours=hours)
    return {"run_id": name, "plan_id": "p", "job_id": "compile", "status": "passed",
            "started_at": end - timedelta(minutes=10), "finished_at": end}


class AppendableSource:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def historical(self, at, rid, limit):
        cursor = (timestamp(at), rid)
        self.calls.append((cursor, limit))
        return sorted((row for row in self.rows if
                       (timestamp(row["finished_at"]), row["run_id"]) > cursor),
                      key=lambda row: (timestamp(row["finished_at"]), row["run_id"]))[:limit]


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr("vedrfolnir.service.utcnow", lambda: NOW)
    engine = make_engine(f"sqlite:///{tmp_path / 'history.sqlite3'}")
    factory = create_session_factory(engine)
    config = replace(settings, mode="files", history_reconcile_days=7,
                     import_batch=2, import_limit_per_poll=2,
                     history_reconcile_limit_per_poll=2)
    svc = EngineService(factory, config)
    yield svc, factory, engine
    engine.dispose()


def test_late_row_behind_forward_cursor_is_imported_once(state):
    svc, factory, _ = state
    source = AppendableSource([finished("a", 8), finished("c", 1)])
    assert svc.import_history(source) == 2
    with factory() as session:
        saved = session.get(SyncState, "history_cursor").value
    source.rows.append(finished("b", 4))
    assert svc.import_history(source) == 0
    assert svc.reconcile_history(source) == 1
    with factory() as session:
        assert session.get(HistoricalRun, "b") is not None
        assert session.get(SyncState, "history_cursor").value == saved
    assert svc.reconcile_history(source) == 0


def test_tied_timestamp_late_lexical_id_recovers(state):
    svc, factory, _ = state
    source = AppendableSource([finished("a", 1), finished("z", 1)])
    assert svc.import_history(source) == 2
    source.rows.append(finished("m", 1))
    assert svc.reconcile_history(source) == 1
    assert svc.reconcile_history(source) == 0
    with factory() as s:
        assert set(s.scalars(select(HistoricalRun.run_id))) == {"a", "m", "z"}


def test_independent_window_cursor_resumes_across_restart_and_budget(state):
    svc, factory, _ = state
    source = AppendableSource([finished(str(i), 7 - i) for i in range(1, 6)])
    assert svc.reconcile_history(source) == 2
    with factory() as s:
        assert s.get(SyncState, "history_reconcile_cursor") is not None
        assert s.get(SyncState, "history_reconcile_until") is not None
    # Reconstructed service reuses the saved reconciliation cursor.
    restarted = EngineService(factory, svc.config)
    assert restarted.reconcile_history(source) == 2
    assert restarted.reconcile_history(source) == 1
    with factory() as s:
        assert set(s.scalars(select(HistoricalRun.run_id))) == {"1", "2", "3", "4", "5"}
        assert s.get(SyncState, "history_reconcile_cursor") is None
        assert s.get(SyncState, "history_reconcile_until") is None


def test_forward_import_is_not_starved_by_reconciliation(state):
    svc, factory, _ = state
    tiny = EngineService(factory, replace(
        svc.config, import_limit_per_poll=1, history_reconcile_limit_per_poll=1))
    source = AppendableSource([finished("1", 3), finished("2", 2), finished("3", 1)])
    assert tiny.import_history(source) == 1
    # The one-row scan revisits an already imported key without adding it.
    assert tiny.reconcile_history(source) == 0
    assert tiny.import_history(source) == 1
    assert tiny.import_history(source) == 1
    with factory() as session:
        assert set(session.scalars(select(HistoricalRun.run_id))) == {"1", "2", "3"}


def test_reconcile_page_error_rolls_back_rows_and_checkpoint(state):
    svc, factory, engine = state
    source = AppendableSource([finished("a", 3), finished("b", 2), finished("c", 1)])
    def fail(conn, cursor, statement, params, context, many):
        if ("sync_state" in statement and statement.startswith(("INSERT", "UPDATE"))
                and "history_reconcile_cursor" in params):
            raise RuntimeError("injected checkpoint failure")
    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="checkpoint failure"):
            svc.reconcile_history(source)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    with factory() as session:
        assert session.get(HistoricalRun, "a") is None
        assert session.get(SyncState, "history_reconcile_cursor") is None
    assert svc.reconcile_history(source) == 2


def test_window_limit_is_explicit_and_can_be_disabled(state):
    svc, factory, _ = state
    source = AppendableSource([finished("older", 24 * 8)])
    assert svc.reconcile_history(source) == 0
    disabled = EngineService(factory, replace(svc.config, history_reconcile_days=0))
    assert disabled.reconcile_history(source) == 0
    with factory() as session:
        assert session.get(HistoricalRun, "older") is None


def test_late_insert_behind_partial_scan_caught_on_next_full_cycle(state):
    svc, factory, _ = state
    source = AppendableSource([finished("a", 4), finished("c", 2),
                               finished("z", 1)])
    assert svc.reconcile_history(source) == 2
    # This late row sorts behind the persisted replay cursor. It cannot
    # appear mid-scan, but is recovered after the next window begins.
    source.rows.append(finished("b", 3))
    assert svc.reconcile_history(source) == 1
    with factory() as s:
        assert s.get(HistoricalRun, "b") is None
    assert svc.reconcile_history(source) == 1
    with factory() as s:
        assert s.get(HistoricalRun, "b") is not None


def test_reconcile_rejects_disordered_source_without_committing(state):
    svc, factory, _ = state
    class Broken(AppendableSource):
        def historical(self, at, rid, limit):
            return [finished("z", 2), finished("a", 3)]
    with pytest.raises(ValueError, match="historical_cursor_order_invalid"):
        svc.reconcile_history(Broken([]))
    with factory() as s:
        assert list(s.scalars(select(HistoricalRun.run_id))) == []
        assert s.get(SyncState, "history_reconcile_cursor") is None
        assert s.get(SyncState, "history_cursor") is None


def test_tick_reconciles_late_source_rows_without_moving_forward_key(state, monkeypatch):
    svc, factory, _ = state
    source = AppendableSource([finished("a", 3), finished("c", 1)])
    source.live = lambda since: ([], [])
    source.pools = lambda: []
    monkeypatch.setattr("vedrfolnir.service.CSVSource", lambda directory: source)
    first = svc.tick()
    assert first["status"] == "ok"
    assert first["imported"] == 2
    assert first["reconciled"] == 0
    source.rows.append(finished("b", 2))
    # The first poll checkpointed a full two-row reconciliation page.
    # The next poll completes it, then the following poll starts a new
    # overlapping cycle and finds the late insertion behind that checkpoint.
    assert svc.tick()["reconciled"] == 0
    last = svc.tick()
    assert last["reconciled"] == 1
    assert last["retention_pruned"] == {"historical_runs": 0, "plan_predictions": 0}
    assert svc.last_reconciled == 1
    with factory() as s:
        assert set(s.scalars(select(HistoricalRun.run_id))) == {"a", "b", "c"}
        assert s.get(SyncState, "history_cursor").value.endswith("|c")


def test_large_expired_backfill_does_not_outgrow_cleanup_budget(state, monkeypatch):
    """Importing 20 expired rows per poll cannot overwhelm a cleanup batch of 2."""
    svc, factory, _ = state
    tuned = EngineService(factory, replace(
        svc.config, import_limit_per_poll=20, retention_batch=2,
        history_retention_days=365, history_reconcile_days=7))
    source = AppendableSource([
        finished(f"old-{i:04d}", 24 * 500 - i)
        for i in range(60)
    ])
    source.live = lambda since: ([], [])
    source.pools = lambda: []
    monkeypatch.setattr("vedrfolnir.service.CSVSource", lambda _: source)

    for _ in range(3):
        result = tuned.tick()
        assert result["imported"] == 20
        assert result["reconciled"] == 0
        assert result["retention_pruned"]["historical_runs"] == 20
        with factory() as session:
            assert list(session.scalars(select(HistoricalRun.run_id))) == []
            assert session.get(SyncState, "history_cursor") is not None

    # The forward cursor remembers the end of the old source even though
    # those rows were dropped. They are not ingested again on subsequent polls.
    tail = tuned.tick()
    assert tail["imported"] == 0
    assert tail["retention_pruned"]["historical_runs"] == 0
