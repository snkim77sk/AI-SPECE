import budget_vnext as budget
from vnext_collection import verified_checkpoint
from vnext_store import get_checkpoint

DAY = '2026-09-01'
SCOPE = '2026:' + DAY


def _row(code='A'):
    return {
        'fyr': '2026',
        'wa_laf_cd': 'WA',
        'laf_cd': 'LA',
        'dept_cd': 'DEPT',
        'dbiz_cd': code,
        'acnt_dv_cd': 'ACCOUNT',
        'dbiz_nm': '일반 행정사업',
    }


def test_unknown_total_first_short_page_requires_explicit_empty_confirmation(monkeypatch):
    calls = []

    def fetch(*args, page=1, **kwargs):
        calls.append(page)
        batch = [_row()] if page == 1 else []
        return batch, None, 'INFO-000', ''

    monkeypatch.setattr(budget, 'fetch_budget_page', fetch)

    first = budget.collect_full_budget(2026, DAY, page_size=2, max_pages=1)
    assert first['status'] == 'RUNNING'
    assert first['complete'] is False
    assert first['fetched'] == 1
    assert first['source_total'] is None
    assert calls == [1]
    assert not verified_checkpoint(get_checkpoint('budget', SCOPE))

    second = budget.collect_full_budget(2026, DAY, page_size=2, max_pages=1)
    assert second['status'] == 'COMPLETE'
    assert second['complete'] is True
    assert second['fetched'] == 1
    assert second['completion_reason'] == 'EMPTY_PAGE'
    assert calls == [1, 2]
    assert verified_checkpoint(get_checkpoint('budget', SCOPE))
