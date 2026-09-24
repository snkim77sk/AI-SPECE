import json

import pytest

import budget_vnext
import db
import vnext_collection
import vnext_store


def _row(name, business_code):
    return {
        "fyr": "2026",
        "exe_ymd": "20260923",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "dept_cd": "D1",
        "dbiz_cd": business_code,
        "dbiz_nm": name,
        "acnt_dv_cd": "A1",
    }


def test_partial_resume_fails_closed_when_overlapping_scope_changes_current_raw(monkeypatch):
    original = _row("LED 가로등 교체", "P1")
    updated = dict(original, dbiz_nm="LED 가로등 교체 변경")
    second = _row("도로조명 유지보수", "P2")

    first_calls = []

    def first_fetch(year, snapshot, keyword, page=1, size=1000, *, region_code="", **kwargs):
        first_calls.append((region_code, page))
        if region_code:
            return [dict(updated)], 1, "INFO-000", ""
        if page == 1:
            return [dict(original)], 2, "INFO-000", ""
        return [dict(second)], 2, "INFO-000", ""

    monkeypatch.setattr(budget_vnext, "fetch_budget_page", first_fetch)

    partial = budget_vnext.collect_full_budget(
        2026, "2026-09-23", page_size=1, max_pages=1, resume=False
    )
    assert partial["complete"] is False
    assert partial["fetched"] == 1
    assert first_calls == [("", 1)]

    regional = budget_vnext.collect_full_budget(
        2026,
        "2026-09-23",
        region_code="4100000",
        page_size=1,
        resume=False,
    )
    assert regional["complete"] is True

    key = budget_vnext._source_key(original, 2026, "2026-09-23")
    with db.connect() as conn:
        current = conn.execute(
            "SELECT payload_json FROM raw_records WHERE dataset='budget' AND source_key=?",
            (key,),
        ).fetchone()
        revisions = conn.execute(
            "SELECT COUNT(*) FROM raw_record_revisions "
            "WHERE dataset='budget' AND source_key=?",
            (key,),
        ).fetchone()[0]
    assert json.loads(current["payload_json"])["dbiz_nm"] == "LED 가로등 교체 변경"
    assert revisions == 2

    resume_calls = []

    def should_not_fetch(*args, **kwargs):
        resume_calls.append((args, kwargs))
        raise AssertionError("stale partial resume must fail before source traffic")

    monkeypatch.setattr(budget_vnext, "fetch_budget_page", should_not_fetch)

    with pytest.raises(
        ValueError,
        match="resume receipts/current RAW changed; replay explicitly with resume=False",
    ):
        budget_vnext.collect_full_budget(
            2026, "2026-09-23", page_size=1, resume=True
        )
    assert resume_calls == []

    checkpoint = vnext_store.get_checkpoint("budget", "2026:2026-09-23")
    assert checkpoint["status"] == "RUNNING"
    assert checkpoint["page_no"] == 2
    assert checkpoint["fetched_count"] == 1

    replay_calls = []

    def replay_fetch(year, snapshot, keyword, page=1, size=1000, *, region_code="", **kwargs):
        replay_calls.append((region_code, page))
        if page == 1:
            return [dict(updated)], 2, "INFO-000", ""
        return [dict(second)], 2, "INFO-000", ""

    monkeypatch.setattr(budget_vnext, "fetch_budget_page", replay_fetch)
    replayed = budget_vnext.collect_full_budget(
        2026, "2026-09-23", page_size=1, resume=False
    )

    assert replayed["complete"] is True
    assert replayed["fetched"] == replayed["saved"] == 2
    assert replay_calls == [("", 1), ("", 2)]
    assert vnext_collection.verified_checkpoint(
        vnext_store.get_checkpoint("budget", "2026:2026-09-23")
    ) is True
