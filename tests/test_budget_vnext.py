import budget_vnext


def test_source_key_is_stable_and_keyword_independent():
    row = {
        "fyr": "2026", "laf_cd": "A", "dept_cd": "B", "dbiz_cd": "C",
        "acnt_dv_cd": "D", "dbiz_nm": "도로시설 개선사업",
    }
    assert budget_vnext._source_key(row, 2026) == budget_vnext._source_key(dict(row), 2026)


def test_collect_full_budget_uses_empty_keyword_and_preserves_all(monkeypatch):
    calls = []
    preserved = []
    checkpoints = []

    pages = {
        1: ([{"dbiz_nm": "일반 행정사업", "dbiz_cd": "1"}, {"dbiz_nm": "도로시설 개선", "dbiz_cd": "2"}], 3, "INFO-000", ""),
        2: ([{"dbiz_nm": "LED 가로등 교체", "dbiz_cd": "3"}], 3, "INFO-000", ""),
    }

    def fake_fetch(year, snapshot, keyword, page=1, size=1000):
        calls.append((year, snapshot, keyword, page, size))
        return pages[page]

    def fake_preserve(rows, year, snapshot):
        preserved.extend(rows)
        return len(rows)

    monkeypatch.setattr(budget_vnext, "fetch_budget_page", fake_fetch)
    monkeypatch.setattr(budget_vnext, "preserve_budget_rows", fake_preserve)
    monkeypatch.setattr(budget_vnext, "save_checkpoint", lambda dataset, scope, **values: checkpoints.append((dataset, scope, values)))
    monkeypatch.setattr("vnext_store.get_checkpoint", lambda dataset, scope: None)

    result = budget_vnext.collect_full_budget(2026, "2026-09-15", page_size=2)

    assert [call[2] for call in calls] == ["", ""]
    assert [row["dbiz_cd"] for row in preserved] == ["1", "2", "3"]
    assert result["fetched"] == 3
    assert result["saved"] == 3
    assert result["complete"] is True
    assert checkpoints[-1][2]["status"] == "COMPLETE"


def test_canary_max_pages_keeps_partial_budget_resumable(monkeypatch):
    calls = []
    checkpoints = []
    monkeypatch.setattr(
        budget_vnext,
        "fetch_budget_page",
        lambda year, snapshot, keyword, page=1, size=1000: (calls.append((keyword, page)) or ([{"dbiz_cd": "1"}], 9999, "INFO-000", "")),
    )
    monkeypatch.setattr(budget_vnext, "preserve_budget_rows", lambda rows, year, snapshot: len(rows))
    monkeypatch.setattr(budget_vnext, "save_checkpoint", lambda dataset, scope, **values: checkpoints.append((dataset, scope, values)))
    monkeypatch.setattr("vnext_store.get_checkpoint", lambda dataset, scope: None)

    result = budget_vnext.collect_full_budget(2026, "2026-09-15", page_size=1, max_pages=1)
    assert calls == [("", 1)]
    assert result["fetched"] == 1
    assert result["complete"] is False
    assert checkpoints[-1][2]["status"] == "RUNNING"
    assert checkpoints[-1][2]["page_no"] == 2


def test_missing_total_full_budget_page_never_marks_complete(monkeypatch):
    checkpoints = []
    monkeypatch.setattr(
        budget_vnext,
        "fetch_budget_page",
        lambda year, snapshot, keyword, page=1, size=1000: ([{"dbiz_cd": str(i)} for i in range(size)], 0, "INFO-000", ""),
    )
    monkeypatch.setattr(budget_vnext, "preserve_budget_rows", lambda rows, year, snapshot: len(rows))
    monkeypatch.setattr(budget_vnext, "save_checkpoint", lambda dataset, scope, **values: checkpoints.append((dataset, scope, values)))
    monkeypatch.setattr("vnext_store.get_checkpoint", lambda dataset, scope: None)

    result = budget_vnext.collect_full_budget(2026, "2026-09-15", page_size=2, max_pages=1)
    assert result["fetched"] == 2
    assert result["source_total"] == 0
    assert result["complete"] is False
    assert checkpoints[-1][2]["status"] == "RUNNING"
    assert checkpoints[-1][2]["page_no"] == 2
