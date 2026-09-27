"""Interrupted budget collection must resume from evidence, not counters alone."""
import json

import pytest

import budget_vnext as budget
import db
from vnext_collection import verified_checkpoint
from vnext_store import get_checkpoint

DAY = "2026-09-15"
SCOPE = "2026:" + DAY


def _row(code, amount=100):
    return {"fyr": "2026", "dbiz_cd": code, "budgetAmount": amount}


def _collect(**kwargs):
    return budget.collect_full_budget(2026, DAY, page_size=1, **kwargs)


def _generation():
    return json.loads(get_checkpoint("budget", SCOPE)["cursor_value"])["generation"]


def test_overlapping_page_raw_change_requires_replay_before_resume(monkeypatch):
    calls = []

    def anomalous(*args, page=1, **kwargs):
        calls.append(page)
        return [_row("A", 100 if page == 1 else 200)], 2, "INFO-000", ""

    monkeypatch.setattr(budget, "fetch_budget_page", anomalous)
    stopped = _collect()
    assert stopped["reason"] == "REPEATED_OR_OVERLAPPING_PAGE"
    assert stopped["fetched"] == stopped["saved"] == 1
    original_generation = _generation()

    def recovered(*args, page=1, **kwargs):
        calls.append(page)
        return [_row("A", 200) if page == 1 else _row("B")], 2, "INFO-000", ""

    monkeypatch.setattr(budget, "fetch_budget_page", recovered)
    result = _collect()

    assert result["complete"] is True
    assert verified_checkpoint(get_checkpoint("budget", SCOPE)) is True
    assert calls == [1, 2, 1, 2]
    assert _generation() != original_generation
    with db.connect() as conn:
        # The anomalous response and original revision remain available for audit.
        assert conn.execute("SELECT COUNT(*) FROM raw_record_revisions").fetchone()[0] == 3


@pytest.mark.parametrize("field,value", [
    ("source_total", 2),
    ("fetched_count", 2),
    ("saved_count", 0),
    ("page_no", 3),
    ("page_size", 2),
])
def test_partial_counter_mismatch_replays_within_page_budget(monkeypatch, field, value):
    calls = []

    def fetch(*args, page=1, **kwargs):
        calls.append(page)
        # Later pages may omit a total; the first page's receipt remains binding.
        return [_row(str(page))], 3 if page == 1 else None, "INFO-000", ""

    monkeypatch.setattr(budget, "fetch_budget_page", fetch)
    first = _collect(max_pages=1)
    assert first["status"] == "RUNNING"
    original_generation = _generation()
    with db.connect() as conn:
        conn.execute(
            f"UPDATE collection_checkpoints SET {field}=? WHERE dataset=? AND scope_key=?",
            (value, "budget", SCOPE),
        )

    restarted = _collect(max_pages=1)
    assert restarted["complete"] is False
    assert restarted["source_total"] == 3
    assert restarted["fetched"] == restarted["saved"] == 1
    assert calls == [1, 1]
    assert _generation() != original_generation

    finished = _collect()
    assert finished["complete"] is True
    assert finished["fetched"] == finished["saved"] == 3
    assert calls == [1, 1, 2, 3]
    assert verified_checkpoint(get_checkpoint("budget", SCOPE)) is True


def test_explicit_replay_failure_cannot_reuse_previous_complete_generation(monkeypatch):
    calls = []

    def fetch(*args, page=1, **kwargs):
        calls.append(page)
        return [_row(str(page))], 2, "INFO-000", ""

    monkeypatch.setattr(budget, "fetch_budget_page", fetch)
    assert _collect()["complete"] is True
    original_generation = _generation()

    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic source failure")

    monkeypatch.setattr(budget, "fetch_budget_page", unavailable)
    with pytest.raises(RuntimeError, match="synthetic source failure"):
        _collect(resume=False, max_pages=1)
    failed = get_checkpoint("budget", SCOPE)
    assert failed["status"] == "FAILED"
    assert failed["fetched_count"] == failed["saved_count"] == 0
    assert _generation() != original_generation
    assert verified_checkpoint(failed) is False

    monkeypatch.setattr(budget, "fetch_budget_page", fetch)
    partial = _collect(max_pages=1)
    assert partial["complete"] is False
    assert partial["fetched"] == partial["saved"] == 1
    replay_generation = _generation()
    finished = _collect(max_pages=1)
    assert finished["complete"] is True
    assert calls == [1, 2, 1, 2]
    assert _generation() == replay_generation
    assert verified_checkpoint(get_checkpoint("budget", SCOPE)) is True
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM raw_record_revisions").fetchone()[0] == 2


@pytest.mark.parametrize("interruption", ["failure", "empty"])
def test_valid_partial_retry_keeps_generation_and_next_page(monkeypatch, interruption):
    calls = []

    def interrupted(*args, page=1, **kwargs):
        calls.append(page)
        if page == 2:
            if interruption == "failure":
                raise RuntimeError("synthetic interruption")
            return [], 2, "INFO-000", ""
        return [_row("A")], 2, "INFO-000", ""

    monkeypatch.setattr(budget, "fetch_budget_page", interrupted)
    if interruption == "failure":
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            _collect()
    else:
        assert _collect()["reason"] == "PREMATURE_EMPTY_PAGE"
    original_generation = _generation()

    def recovered(*args, page=1, **kwargs):
        calls.append(page)
        return [_row("B")], 2, "INFO-000", ""

    monkeypatch.setattr(budget, "fetch_budget_page", recovered)
    assert _collect()["complete"] is True
    assert calls == [1, 2, 2]
    assert _generation() == original_generation
    assert verified_checkpoint(get_checkpoint("budget", SCOPE)) is True
    assert _collect()["resumed"] is True
    assert calls == [1, 2, 2]
