import copy
import json

import pytest
import db
import smartled_contract_vnext as adapter
import smartled_contract_store_vnext as store


@pytest.fixture(autouse=True)
def schema():
    store.ensure_schema()


def snapshot(key='U1', code='39119802', decided='R26TA0000000100'):
    return adapter.normalize_snapshot(
        {'untyCntrctNo': key, 'dcsnCntrctNo': decided, 'cntrctCnclsDate': '2026-10-01',
         'cntrctNm': '스마트LED', 'thtmCntrctAmt': '1000'},
        [{'untyCntrctNo': key, 'dcsnCntrctNo': decided, 'prdctClsfcNo': code,
          'prdctIdntNo': 'P1', 'prdctAmt': '100'}], complete=True, expected_count=1)


def test_queue_cap_no_dropped_jobs_and_repeat_updates_existing(monkeypatch):
    monkeypatch.setattr(store, 'MAX_PENDING', 2)
    r1 = store.enqueue('U1', 'NEW')
    store.enqueue('U2', 'RECHECK')
    with pytest.raises(store.QueueFullError):
        store.enqueue('U3')
    r2 = store.enqueue('U1', 'CHANGE')
    assert r2 > r1
    assert store.next_pending()['reasons'] == 'CHANGE,NEW'
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) AS n FROM smartled_contract_queue').fetchone()['n'] == 2


def test_page_queue_and_cursor_atomic_on_cap(monkeypatch):
    monkeypatch.setattr(store, 'MAX_PENDING', 2)
    store.enqueue('EXISTING')
    with pytest.raises(store.QueueFullError):
        store.enqueue_page_and_cursor(['U1', 'U2'], 'smartled_contract_cursor', '{"page":2}')
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) AS n FROM smartled_contract_queue').fetchone()['n'] == 1
        assert conn.execute("SELECT value FROM app_settings WHERE key='smartled_contract_cursor'").fetchone() is None
    store.enqueue_page_and_cursor(['U1', 'U1'], 'smartled_contract_cursor', '{"page":2}')
    assert json.loads(db.get_setting('smartled_contract_cursor', '{}')) == {'page': 2}


def test_refresh_commit_replaces_revision_without_duplicates_and_removes_queue():
    revision = store.enqueue('U1')
    store.save_snapshot(snapshot(), queue_revision=revision)
    assert store.next_pending() is None
    revision = store.enqueue('U1', 'CHANGE')
    store.save_snapshot(snapshot(decided='R26TA0000000101'), queue_revision=revision)
    rows = store.list_current()
    assert len(rows) == 1 and rows[0]['decided_no'] == 'R26TA0000000101'
    assert 'snapshot_json' not in rows[0]
    assert rows[0]['fixture_amount'] == '100' and rows[0]['contract_amount'] == '1000'


def test_partial_invalid_snapshot_keeps_current_and_queue():
    store.save_snapshot(snapshot())
    revision = store.enqueue('U1', 'CHANGE')
    bad = snapshot(); bad['source_detail_count'] = 0
    with pytest.raises(ValueError):
        store.save_snapshot(bad, queue_revision=revision)
    assert store.get_snapshot('U1')['snapshot']['decided_contract_no'] == 'R26TA0000000100'
    assert store.next_pending()['revision'] == revision


def test_complete_no_match_retires_old_smart_evidence():
    store.save_snapshot(snapshot())
    revision = store.enqueue('U1', 'CHANGE')
    store.save_snapshot(snapshot(code='39111603'), queue_revision=revision)
    assert store.list_current() == []
    assert store.get_snapshot('U1')['snapshot']['smartled_match'] is False
    assert store.next_pending() is None


def test_kinds_are_filtered_without_combining_control_or_lamp():
    store.save_snapshot(snapshot('U1'))
    store.save_snapshot(snapshot('U2', '39119801'))
    store.save_snapshot(snapshot('U3', '39119804'))
    assert [r['unified_no'] for r in store.list_current(kind='FIXTURE')] == ['U1']
    assert [r['unified_no'] for r in store.list_current(kind='LAMP')] == ['U2']
    assert [r['unified_no'] for r in store.list_current(kind='CONTROL')] == ['U3']
    assert store.list_current(start_date='2026-10-02') == []


def test_new_queue_change_prevents_stale_save():
    store.save_snapshot(snapshot())
    revision = store.enqueue('U1')
    store.enqueue('U1', 'DELETE')
    with pytest.raises(store.StaleRefreshError):
        store.save_snapshot(snapshot(decided='R26TA0000000101'), queue_revision=revision)
    assert store.get_snapshot('U1')['snapshot']['decided_contract_no'] == 'R26TA0000000100'
    assert store.next_pending()['revision'] == revision + 1


def test_source_delete_keeps_history_but_invalidates_inflight_refresh():
    store.save_snapshot(snapshot())
    revision = store.enqueue('U1', 'RECHECK')
    event = adapter.deletion_event({'untyCntrctNo': 'U1', 'chgDivNm': 'DEL', 'dltDt': '2026-10-22 10:00:00'})
    store.mark_source_deleted(event)
    assert store.next_pending() is None and store.list_current() == []
    assert store.get_snapshot('U1')['source_state'] == 'SOURCE_DELETED'
    assert store.get_snapshot('U1')['snapshot']['items']
    with pytest.raises(store.StaleRefreshError):
        store.save_snapshot(snapshot(), queue_revision=revision)
    assert event['legal_cancellation_confirmed'] is False


def test_deletion_before_first_discovery_is_supported():
    event = adapter.deletion_event({'untyCntrctNo': 'U1', 'chgDivNm': 'DEL', 'dltDt': '2026-10-22 10:00:00'})
    assert store.mark_source_deleted(event) is False
    assert store.get_snapshot('U1') is None
    store.enqueue('U1')
    store.mark_source_deleted(event)
    assert store.get_snapshot('U1')['source_state'] == 'SOURCE_DELETED'
    assert store.list_current() == []


def test_failed_source_refresh_does_not_modify_store():
    store.save_snapshot(snapshot())
    revision = store.enqueue('U1')
    with pytest.raises(adapter.ContractResponseError):
        adapter.refresh_snapshot({'untyCntrctNo': 'U1', 'cntrctCnclsDate': '2026-10-01'},
                                 fetch_detail=lambda *args: ([], 1))
    assert store.next_pending()['revision'] == revision
    assert store.get_snapshot('U1')['source_state'] == 'CURRENT'


def test_read_path_does_not_run_schema(monkeypatch):
    store.save_snapshot(snapshot())
    monkeypatch.setattr(store, 'ensure_schema', lambda: pytest.fail('read must not run DDL'))
    assert len(store.list_current()) == 1
    assert store.get_snapshot('U1')
    assert store.next_pending() is None


def test_bad_filters_and_bytes_do_not_modify_state(monkeypatch):
    with pytest.raises(ValueError):
        store.list_current(start_date='2026-09-30')
    with pytest.raises(ValueError):
        store.list_current(kind='ALL')
    with pytest.raises(ValueError):
        store.list_current(offset=50001)
    original = store._json
    monkeypatch.setattr(store, '_json', lambda value, *a: original(value, 20))
    with pytest.raises(ValueError, match='BYTES_EXCEEDED'):
        store.save_snapshot(snapshot())
    assert store.get_snapshot('U1') is None


def test_failed_jobs_block_after_three_preserved_and_not_reset_by_repeat():
    r = store.enqueue('U1')
    for _ in range(3):
        store.mark_failed('U1', 'ContractResponseError', queue_revision=r)
    assert store.next_pending() is None
    assert store.contains('U1')
    assert store.queue_status() == {'pending': 1, 'blocked': 1}
    store.enqueue('U1', 'CHANGE')
    assert store.next_pending() is None
    assert store.queue_status() == {'pending': 1, 'blocked': 1}
    store.enqueue('U2')
    assert store.next_pending()['unified_no'] == 'U2'


def test_enqueue_list_rolls_back_full_page_if_cap_reached(monkeypatch):
    monkeypatch.setattr(store, 'MAX_PENDING', 1)
    with pytest.raises(store.QueueFullError):
        store.enqueue(['U1', 'U2'])
    assert store.queue_status() == {'pending': 0, 'blocked': 0}


def test_summary_items_bounded_and_search_is_literal():
    value = snapshot()
    value['items'][0]['item_name'] = '긴등기구이름' * 100
    value['contract_name'] = '스마트LED 50%_test'
    store.save_snapshot(value)
    row = store.list_current(q='50%_')[0]
    summary = row['items_summary']['FIXTURE']
    assert summary['count'] == 1 and len(summary['items']) == 1
    assert summary['items'][0]['name'].endswith('…')
    assert len(json.dumps(row['items_summary'], ensure_ascii=False).encode()) < 4096
    assert store.list_current(q='50__') == []
    assert store.list_current(q='missing') == []


def test_retry_after_day_requires_new_observation(monkeypatch):
    monkeypatch.setattr(store, '_now', lambda: '2026-10-22T10:00:00+00:00')
    store.enqueue('U1')
    for _ in range(3):
        store.mark_failed('U1', 'TimeoutError')
    monkeypatch.setattr(store, '_now', lambda: '2026-10-22T23:00:00+00:00')
    store.enqueue('U1')
    assert store.next_pending() is None
    monkeypatch.setattr(store, '_now', lambda: '2026-10-23T10:00:00+00:00')
    assert store.next_pending() is None
    store.enqueue('U1', 'CHANGE')
    assert store.next_pending()['attempts'] == 0


def test_corrected_before_scope_date_retires_current_not_history():
    store.save_snapshot(snapshot())
    rev = store.enqueue('U1', 'CHANGE')
    store.mark_out_of_scope('U1', queue_revision=rev)
    assert store.list_current() == [] and store.next_pending() is None
    assert store.get_snapshot('U1')['source_state'] == 'OUT_OF_SCOPE'
    assert store.get_snapshot('U1')['snapshot']['items']


def test_duplicate_items_preserved_summary_capped_per_kind():
    value = snapshot()
    value['items'] = [dict(value['items'][0], source_position=i, kind=kind,
                           item_name='등' * 100, item_amount='9' * 64)
                      for kind in ('FIXTURE', 'LAMP', 'CONTROL') for i in range(4)]
    value['source_detail_count'] = 12
    store.save_snapshot(value)
    row = store.list_current()[0]
    for kind in ('FIXTURE', 'LAMP', 'CONTROL'):
        assert row['items_summary'][kind]['count'] == 4
        assert len(row['items_summary'][kind]['items']) == 3
    assert len(store.get_snapshot('U1')['snapshot']['items']) == 12
    assert len(json.dumps(row['items_summary'], ensure_ascii=False).encode()) < 4096


def test_deleted_then_requeued_id_never_reuses_revision():
    old = store.enqueue('U1')
    event = adapter.deletion_event({'untyCntrctNo': 'U1', 'chgDivNm': 'DEL', 'dltDt': '2026-10-22 10:00:00'})
    store.mark_deleted(event)
    new = store.enqueue('U1')
    assert new > old
    with pytest.raises(store.StaleRefreshError):
        store.save_snapshot(snapshot(), queue_revision=old)
    assert store.get_snapshot('U1')['source_state'] == 'SOURCE_DELETED'
    assert store.next_pending()['revision'] == new


@pytest.mark.parametrize('number', ['1e99999999','1e-99999999','9'*129,'0e99999999'])
def test_store_rejects_numeric_allocation_bombs(number):
    with pytest.raises(ValueError):
        store._amount(number)
