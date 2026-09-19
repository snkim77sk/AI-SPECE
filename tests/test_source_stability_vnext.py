import datetime as dt
import json

import pytest

import budget_snapshot_vnext
import historical_vnext
import vnext_stability
from vnext_collection import collect_pages, verified_checkpoint
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint


def _collect(dataset, scope, pages, *, page_size=2):
    def fetch(page, size):
        return list(pages.get(page, [])), None
    return collect_pages(
        dataset=dataset,
        scope=scope,
        range_start='2026-09-01',
        range_end='2026-09-01',
        page_size=page_size,
        max_pages=10,
        resume=True,
        fetch=fetch,
        identity=lambda row: row['id'],
        source_system='TEST',
        source_operation='TEST_LIST',
        source_date=lambda row: '2026-09-01',
        preserve=preserve_raw,
        checkpoint=save_checkpoint,
        lookup=get_checkpoint,
    )


def _rewrite_stability_timestamp(dataset, scope, value):
    cp = get_checkpoint(dataset, scope)
    meta = json.loads(cp['cursor_value'])
    if value is None:
        meta['stability'].pop('verified_at_utc', None)
    else:
        meta['stability']['verified_at_utc'] = value.isoformat()
    save_checkpoint(
        dataset, scope,
        cursor_value=json.dumps(meta, sort_keys=True),
        range_start=cp['range_start'], range_end=cp['range_end'],
        page_no=cp['page_no'], page_size=cp['page_size'],
        last_page_fingerprint=cp['last_page_fingerprint'],
        source_total=cp['source_total'], fetched_count=cp['fetched_count'],
        saved_count=cp['saved_count'], status=cp['status'], last_error=cp['last_error'],
    )


def test_stability_replay_marks_receipt_generation_verified():
    pages = {1: [{'id': 'A'}, {'id': 'B'}], 2: [{'id': 'C'}]}
    result = _collect('stable_dataset', 'scope', pages)
    assert result['complete']
    cp = get_checkpoint('stable_dataset', 'scope')
    assert verified_checkpoint(cp)
    assert not vnext_stability.stability_verified_checkpoint(cp)
    assert vnext_stability.stability_verified_at(cp) == ''

    verified = vnext_stability.verify_checkpoint_source(
        dataset='stable_dataset', scope='scope',
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row['id'],
    )
    assert verified['stable'] is True
    assert verified['reason'] == 'VERIFIED'
    assert verified['replayed_pages'] == 2
    stamp = verified['verified_at_utc']
    parsed = dt.datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None
    cp = get_checkpoint('stable_dataset', 'scope')
    assert vnext_stability.stability_verified_checkpoint(cp)
    assert vnext_stability.stability_fresh_checkpoint(cp)
    assert vnext_stability.stability_verified_at(cp) == stamp

    repeated = vnext_stability.verify_checkpoint_source(
        dataset='stable_dataset', scope='scope',
        fetch=lambda page, size: (_ for _ in ()).throw(AssertionError('must not refetch')),
        identity=lambda row: row['id'],
    )
    assert repeated['reason'] == 'ALREADY_VERIFIED'
    assert repeated['verified_at_utc'] == stamp


def test_stale_verified_proof_replays_and_refreshes_timestamp():
    pages = {1: [{'id': 'A'}, {'id': 'B'}], 2: [{'id': 'C'}]}
    assert _collect('stale_dataset', 'scope', pages)['complete']
    now = dt.datetime.now(dt.timezone.utc)
    old = now - dt.timedelta(hours=25)
    first = vnext_stability.verify_checkpoint_source(
        dataset='stale_dataset', scope='scope',
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row['id'], now=old,
    )
    assert first['reason'] == 'VERIFIED'
    cp = get_checkpoint('stale_dataset', 'scope')
    assert vnext_stability.stability_verified_checkpoint(cp)
    assert not vnext_stability.stability_fresh_checkpoint(cp, now=now)

    calls = []
    refreshed = vnext_stability.verify_checkpoint_source(
        dataset='stale_dataset', scope='scope',
        fetch=lambda page, size: (calls.append(page) or list(pages.get(page, [])), None),
        identity=lambda row: row['id'], now=now,
    )
    assert refreshed['reason'] == 'VERIFIED'
    assert calls == [1, 2]
    cp = get_checkpoint('stale_dataset', 'scope')
    assert vnext_stability.stability_fresh_checkpoint(cp, now=now)
    assert vnext_stability.stability_verified_at(cp) == now.isoformat()


def test_legacy_or_tampered_verified_without_timestamp_is_not_fresh_and_is_replayed():
    pages = {1: [{'id': 'A'}]}
    assert _collect('legacy_stable_dataset', 'scope', pages)['complete']
    vnext_stability.verify_checkpoint_source(
        dataset='legacy_stable_dataset', scope='scope',
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row['id'],
    )
    _rewrite_stability_timestamp('legacy_stable_dataset', 'scope', None)
    cp = get_checkpoint('legacy_stable_dataset', 'scope')
    assert not vnext_stability.stability_verified_checkpoint(cp)
    assert not vnext_stability.stability_fresh_checkpoint(cp)

    calls = []
    replayed = vnext_stability.verify_checkpoint_source(
        dataset='legacy_stable_dataset', scope='scope',
        fetch=lambda page, size: (calls.append(page) or list(pages.get(page, [])), None),
        identity=lambda row: row['id'],
    )
    assert replayed['reason'] == 'VERIFIED'
    assert calls == [1, 2]
    assert vnext_stability.stability_fresh_checkpoint(
        get_checkpoint('legacy_stable_dataset', 'scope')
    )


def test_stability_age_policy_is_bounded(monkeypatch):
    monkeypatch.setenv('G2B_VNEXT_STABILITY_MAX_AGE_HOURS', '0')
    with pytest.raises(ValueError, match='between 1 and 168'):
        vnext_stability.stability_max_age_hours()
    monkeypatch.setenv('G2B_VNEXT_STABILITY_MAX_AGE_HOURS', '169')
    with pytest.raises(ValueError, match='between 1 and 168'):
        vnext_stability.stability_max_age_hours()
    monkeypatch.setenv('G2B_VNEXT_STABILITY_MAX_AGE_HOURS', 'abc')
    with pytest.raises(ValueError, match='must be an integer'):
        vnext_stability.stability_max_age_hours()


def test_stability_replay_detects_shift_without_overlap_and_forces_fresh_generation():
    original = {
        1: [{'id': 'A'}, {'id': 'B'}],
        2: [{'id': 'C'}, {'id': 'D'}],
        3: [],
    }
    assert _collect('shift_dataset', 'scope', original)['complete']

    shifted = {
        1: [{'id': 'B'}, {'id': 'C'}],
        2: [{'id': 'D'}],
        3: [],
    }
    checked = vnext_stability.verify_checkpoint_source(
        dataset='shift_dataset', scope='scope',
        fetch=lambda page, size: (list(shifted.get(page, [])), None),
        identity=lambda row: row['id'],
    )
    assert checked['stable'] is False
    assert checked['reason'] == 'SOURCE_ORDER_OR_PAYLOAD_CHANGED'
    cp = get_checkpoint('shift_dataset', 'scope')
    marker = json.loads(cp['cursor_value'])
    assert cp['status'] == 'RUNNING'
    assert cp['page_no'] == 1 and cp['fetched_count'] == 0 and cp['saved_count'] == 0
    assert marker['stability_recollect_required'] is True
    assert not verified_checkpoint(cp)


def test_transient_stability_replay_error_does_not_destroy_valid_receipts():
    pages = {1: [{'id': 'A'}]}
    assert _collect('transient_dataset', 'scope', pages)['complete']
    before = get_checkpoint('transient_dataset', 'scope')

    def broken(page, size):
        raise TimeoutError('synthetic transient')

    checked = vnext_stability.verify_checkpoint_source(
        dataset='transient_dataset', scope='scope', fetch=broken,
        identity=lambda row: row['id'],
    )
    assert checked['stable'] is False
    assert checked['reason'] == 'STABILITY_REPLAY_ERROR:TimeoutError'
    after = get_checkpoint('transient_dataset', 'scope')
    assert after['cursor_value'] == before['cursor_value']
    assert after['status'] == 'COMPLETE'
    assert verified_checkpoint(after)
    assert not vnext_stability.stability_verified_checkpoint(after)
    assert vnext_stability.stability_verified_at(after) == ''


def test_historical_checkpoint_requires_fresh_replay_stability(monkeypatch):
    chunk = historical_vnext.BackfillChunk('2026-09-01', '2026-09-01')
    pages = {1: [{'id': 'A'}]}
    _collect('bid_notice_goods', chunk.scope, pages)
    before = historical_vnext.checkpoint_status('bid_notice_goods', chunk)
    assert before['receipt_complete'] is True
    assert before['complete'] is False

    vnext_stability.verify_checkpoint_source(
        dataset='bid_notice_goods', scope=chunk.scope,
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row['id'],
    )
    after = historical_vnext.checkpoint_status('bid_notice_goods', chunk)
    assert after['receipt_complete'] is True
    assert after['stability_verified'] is True
    assert after['stability_fresh'] is True
    assert after['complete'] is True

    _rewrite_stability_timestamp(
        'bid_notice_goods', chunk.scope,
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=25),
    )
    stale = historical_vnext.checkpoint_status('bid_notice_goods', chunk)
    assert stale['stability_verified'] is False
    assert stale['stability_fresh'] is False
    assert stale['complete'] is False


def test_budget_snapshot_audit_requires_fresh_replay_stability(monkeypatch):
    day = '2026-09-01'
    scope = '2026:' + day
    row = {
        'id': 'A', 'fyr': '2026', 'exe_ymd': '20260901',
        'dbiz_cd': 'A', 'dbiz_nm': '일반사업',
    }
    pages = {1: [row]}
    _collect('budget', scope, pages)
    audit = budget_snapshot_vnext.audit_snapshots([day])
    assert audit['records'][0]['receipt_complete'] is True
    assert audit['records'][0]['complete'] is False

    vnext_stability.verify_checkpoint_source(
        dataset='budget', scope=scope,
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda item: item['id'],
    )
    audit = budget_snapshot_vnext.audit_snapshots([day])
    assert audit['records'][0]['stability_verified'] is True
    assert audit['records'][0]['stability_fresh'] is True
    assert audit['all_requested_snapshots_complete'] is True

    _rewrite_stability_timestamp(
        'budget', scope,
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=25),
    )
    stale = budget_snapshot_vnext.audit_snapshots([day])
    assert stale['records'][0]['stability_verified'] is False
    assert stale['records'][0]['stability_fresh'] is False
    assert stale['all_requested_snapshots_complete'] is False
