import datetime as dt
import importlib.util
from pathlib import Path

import pytest


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "g2b_bounded_canary.py"
    spec = importlib.util.spec_from_file_location("g2b_bounded_canary_testmod", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bounded_canary_request_budget_matches_probe_count_and_lookback():
    module = _load_script()
    assert module.G2B_PROBE_COUNT == 6
    assert module.G2B_LOOKBACK_DAYS == 3
    assert module.G2B_MAX_HTTP_REQUESTS == 18
    assert module.G2B_MAX_HTTP_REQUESTS == module.G2B_PROBE_COUNT * module.G2B_LOOKBACK_DAYS
    assert module.LOFIN_MAX_HTTP_REQUESTS == 1
    assert module.PAGE_SIZE == 10


def test_non_live_bounded_canary_performs_no_source_request_and_reports_bounds(tmp_path, monkeypatch):
    module = _load_script()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.delenv("G2B_VNEXT_SOURCE_COMMIT_SHA", raising=False)
    report = module.run_bounded_canary(
        allow_live=False,
        now=dt.datetime(2026, 9, 17, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9))),
    )

    assert report["live_allowed_for_this_invocation"] is False
    assert report["source_commit_sha"] == ""
    assert report["g2b"]["status"] == "NOT_REQUESTED"
    assert report["budget"]["status"] == "NOT_REQUESTED"
    assert report["g2b_max_http_requests"] == 18
    assert report["g2b_lookback_days"] == 3
    assert report["lofin_max_http_requests"] == 1
    assert report["production_db_touched"] is False
    assert report["bulk_collection_attempted"] is False
    assert report["whole_source_completeness_verified"] is False
    assert (tmp_path / "verification" / "canary.json").exists()


def test_live_bounded_canary_requires_runtime_source_commit_before_any_probe(tmp_path, monkeypatch):
    module = _load_script()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.delenv("G2B_VNEXT_SOURCE_COMMIT_SHA", raising=False)
    monkeypatch.setenv("G2B_SERVICE_KEY", "synthetic-do-not-use")
    monkeypatch.setenv("LOFIN_API_KEY", "synthetic-do-not-use")

    with pytest.raises(RuntimeError, match="CANARY_RUNTIME_SOURCE_SHA_REQUIRED"):
        module.run_bounded_canary(
            allow_live=True,
            now=dt.datetime(2026, 9, 17, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9))),
        )
