"""Committed source pages must not remain resident while fetching the next page."""
import weakref

import budget_pg_collection
import budget_pg_store
import vnext_collection
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint


class TrackedRow(dict):
    """Real dict payload with weak references, without a test-owned strong ref."""


def _tracked_fetch():
    previous = []
    calls = []

    def fetch(page, size):
        assert size == 2
        assert all(ref() is None for ref in previous), "previous page retained during fetch"
        calls.append(page)
        rows = [
            TrackedRow(
                id=f"{page}:{index}", fyr="2026", dbiz_cd=f"{page}:{index}",
                amount=100, text=(f"page {page} row {index} 나라장터 " * 4096),
            )
            for index in range(size)
        ]
        previous[:] = [weakref.ref(row) for row in rows]
        return rows, 4

    return fetch, previous, calls


def _check_memory_checkpoint(monkeypatch, module, previous):
    checks = []

    def checkpoint():
        # Each page has prefetch and post-fetch admission checks. The former
        # must see no old page; the latter deliberately sees the new response.
        if len(checks) % 2 == 0:
            assert all(ref() is None for ref in previous)
        else:
            assert all(ref() is not None for ref in previous)
        checks.append(True)

    monkeypatch.setattr(module, "_memory_checkpoint", checkpoint)
    return checks


def test_sqlite_collector_releases_committed_page_after_progress(monkeypatch):
    fetch, previous, calls = _tracked_fetch()
    checks = _check_memory_checkpoint(monkeypatch, vnext_collection, previous)
    events = []

    def progress(event):
        events.append(event)
        if event["event"] == "page_complete":
            # Lifetime ends after callbacks finish, preserving callback behavior.
            assert all(ref() is not None for ref in previous)

    result = vnext_collection.collect_pages(
        dataset="page_lifetime", scope="scope", range_start="2026-10-01",
        range_end="2026-10-01", page_size=2, max_pages=2, resume=True,
        fetch=fetch, identity=lambda row: row["id"], source_system="TEST",
        source_operation="TEST_LIST", source_date=lambda row: "2026-10-01",
        preserve=preserve_raw, checkpoint=save_checkpoint, lookup=get_checkpoint,
        progress=progress,
    )
    assert calls == [1, 2]
    assert len(checks) == 4
    assert result["complete"] is True
    assert result["fetched"] == result["saved"] == 4
    assert [event["page_items"] for event in events if event["event"] == "page_complete"] == [2, 2]
    assert vnext_collection.verified_checkpoint(get_checkpoint("page_lifetime", "scope"))


def test_pg_collector_releases_committed_page_with_test_sqlite_adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("G2B_BUDGET_DATABASE_URL", f"sqlite:///{tmp_path / 'page-lifetime.sqlite3'}")
    for name in ("DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"):
        monkeypatch.delenv(name, raising=False)
    budget_pg_store.reset_engine_cache()
    fetch, previous, calls = _tracked_fetch()
    checks = _check_memory_checkpoint(monkeypatch, budget_pg_collection, previous)
    try:
        result = budget_pg_collection.collect_pages(
            dataset="budget", scope="scope", range_start="2026", range_end="2026-10-01",
            page_size=2, max_pages=2, resume=True, fetch=fetch,
            identity=lambda row: row["dbiz_cd"], source_system="LOFIN",
            source_operation="QWGJK", source_date=lambda row: "2026-10-01",
            checkpoint_contract="TEST_V1",
        )
        assert calls == [1, 2]
        assert len(checks) == 4
        assert result["complete"] is True
        assert result["fetched"] == result["saved"] == 4
        assert budget_pg_collection.verified_checkpoint(budget_pg_store.get_checkpoint("budget", "scope"))
    finally:
        budget_pg_store.reset_engine_cache()
