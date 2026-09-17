import importlib.util
from pathlib import Path

import pytest

import budget_snapshot_vnext
import historical_vnext


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "g2b_small_backfill.py"
SPEC = importlib.util.spec_from_file_location("g2b_small_backfill_validation", SCRIPT)
small = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(small)


def test_small_backfill_requires_explicit_live_unlock():
    with pytest.raises(RuntimeError, match="SMALL_BACKFILL_LIVE_LOCKED"):
        small.run(allow_live=False)


def test_small_backfill_page_budget_is_hard_capped_before_db_access():
    with pytest.raises(ValueError, match="max_pages must be between 1 and 2"):
        small.run(allow_live=True, max_pages=3, date_value="2026-09-01")


def test_small_backfill_rejects_future_or_current_like_date_before_db_access():
    with pytest.raises(ValueError, match="completed past KST date"):
        small.run(allow_live=True, max_pages=1, date_value="2099-01-01")


def test_validation_db_rejects_any_path_outside_verification(monkeypatch, tmp_path):
    monkeypatch.setattr(small, "VERIFY", (tmp_path / "verification").resolve())
    monkeypatch.setenv("G2B_DB_PATH", str(tmp_path / "outside.sqlite3"))
    with pytest.raises(RuntimeError, match="VALIDATION_DB_PATH_UNSAFE"):
        small._validation_db()


def test_validation_db_accepts_only_exact_disposable_name_and_starts_fresh(monkeypatch, tmp_path):
    verify = (tmp_path / "verification").resolve()
    verify.mkdir()
    target = verify / "small_backfill.sqlite3"
    target.write_text("old synthetic data", encoding="utf-8")
    monkeypatch.setattr(small, "VERIFY", verify)
    monkeypatch.setenv("G2B_DB_PATH", str(target))
    assert small._validation_db() == target
    assert not target.exists()


def test_completed_one_day_scope_never_claims_whole_source_completeness(monkeypatch, tmp_path):
    verify = (tmp_path / "verification").resolve()
    verify.mkdir()
    target = verify / "small_backfill.sqlite3"
    monkeypatch.setattr(small, "VERIFY", verify)
    monkeypatch.setattr(small, "_validation_db", lambda: target)
    monkeypatch.setattr(
        historical_vnext,
        "run_backfill",
        lambda *a, **k: {"complete": True, "audit": {"all_complete": True}, "results": []},
    )
    monkeypatch.setattr(historical_vnext, "raw_row_counts", lambda: {"bid_notice_goods": 3})
    monkeypatch.setattr(
        budget_snapshot_vnext,
        "run_snapshots",
        lambda *a, **k: {
            "audit": {"all_requested_snapshots_complete": True, "records": []},
            "results": [],
        },
    )

    report = small.run(
        allow_live=True,
        approval=verify / "synthetic-canary.json",
        date_value="2026-09-01",
        max_pages=1,
    )
    assert report["requested_validation_scope_complete"] is True
    assert report["whole_source_completeness_verified"] is False
    assert report["validation_scope"] == "one completed KST date only"
