"""Attempt-scoped isolated worker errors must never leak to a later run."""
import db
import g2b_heavy_worker


def test_child_failure_receipt_is_tagged_with_parent_attempt(monkeypatch):
    seen = []
    monkeypatch.setattr(db, "set_setting", lambda name, value: seen.append((name, value)))
    attempt = "a" * 32
    monkeypatch.setenv("G2B_HEAVY_WORKER_ATTEMPT_ID", attempt)

    assert g2b_heavy_worker._persist_source_failure_detail(
        "budget", "WORKER:source_run:TimeoutError"
    ) is True
    assert seen == [(
        "budget_recent_last_error",
        f"G2B_WORKER_FAILURE_V1:{attempt}:WORKER:source_run:TimeoutError",
    )]


def test_child_failure_receipt_is_bounded_without_exposing_unrelated_data(monkeypatch):
    seen = []
    monkeypatch.setattr(db, "set_setting", lambda name, value: seen.append((name, value)))
    monkeypatch.setenv("G2B_HEAVY_WORKER_ATTEMPT_ID", "b" * 32)

    assert g2b_heavy_worker._persist_source_failure_detail(
        "shopping", "X" * 900
    ) is True
    assert seen[0][0] == "shopping_recent_last_error"
    assert seen[0][1].startswith("G2B_WORKER_FAILURE_V1:" + "b" * 32 + ":")
    assert len(seen[0][1]) == 180


def test_direct_untracked_worker_retains_legacy_error_format(monkeypatch):
    seen = []
    monkeypatch.setattr(db, "set_setting", lambda name, value: seen.append((name, value)))
    monkeypatch.delenv("G2B_HEAVY_WORKER_ATTEMPT_ID", raising=False)

    assert g2b_heavy_worker._persist_source_failure_detail(
        "budget", "WORKER:source_run:OperationalError"
    ) is True
    assert seen == [("budget_recent_last_error", "WORKER:source_run:OperationalError")]
