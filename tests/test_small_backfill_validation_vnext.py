import importlib.util
from pathlib import Path

import pytest


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
