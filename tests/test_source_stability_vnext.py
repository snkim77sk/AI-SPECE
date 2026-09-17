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
    assert vnext_stability.stability_verified_at(cp) == stamp

    # A second audit call reuses the same proof instead of pretending it was re-run.
    repeated = vnext_stability.verify_checkpoint_source(
        dataset='stable_dataset', scope='scope',
        fetch=lambda page, size: (_ for _ in ()).throw(AssertionError('must not refetch')),
        identity=lambda row: row['id'],
    )
    assert repeated['reason'] == 'ALREADY_VERIFIED'
    assert repeated['verified_at_utc'] == stamp


def test_stability_replay_detects_shift_without_overlap_and_forces_fresh_generation():
    original = {
        1: [{'id': 'A'}, {'id': 'B'}],
        2: [{'id': 'C'}, {'id': 'D'}],
        3: [],
    }
    assert _collect('shift_dataset', 'scope', original)['complete']

    # A disappears between passes. Offset page 2 now returns only D, so C could be
    # silently skipped by a simple overlap-only detector. Replay of page 1 catches it.
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


def test_historical_checkpoint_requires_replay_stability(monkeypatch):
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
    assert after['complete'] is True


def test_budget_snapshot_audit_requires_replay_stability(monkeypatch):
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
    assert audit['all_requested_snapshots_complete'] is True
