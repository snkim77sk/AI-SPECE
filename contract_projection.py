"""Revision-aware contract linking. Unresolved/ambiguous updates retire old facts.

Every contract source keeps a separate derived record. A single award summary never
selects an arbitrary last contract when more than one current contract exists.
"""
import hashlib
import json
import re
from contextlib import contextmanager

from db import connect
from notice_identity_vnext import canonical_notice_order, notice_keys_equivalent, split_notice_key
from vnext_schema import ensure_vnext_schema, NORMALIZER_VERSION
from projection_store_vnext import replace_fact_group, clear_fact_group_by_raw, retire_contract_links
from vnext_store import save_lifecycle_link

DATASET = 'contract_service'
BID_DATASET = 'bid_notice_service'
CONTRACT_DEFAULTS = dict(contract_no='', contract_vendor='', contract_vendor_bizno='', contract_amount=0)


def _pick(row, *names, default=''):
    return next((row[n] for n in names if isinstance(row, dict) and row.get(n) not in (None, '')), default)


def _number(value):
    from decimal import Decimal, InvalidOperation
    try:
        n = Decimal(str(value or 0).replace(',', '').strip())
        return int(n) if n.is_finite() else 0
    except (ValueError, InvalidOperation):
        return 0


def _digits(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def _digest(row):
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def contract_key(row):
    return str(_pick(row, 'dcsnCntrctNo', 'untyCntrctNo', 'contractNo')).strip()


def parse_contract_parties(value):
    """Keep malformed entries as invalid markers; never shrink joint into single."""
    text = str(value or '').strip()
    if not text:
        return []
    chunks = re.split(r'\]\s*,\s*\[|\r?\n', text.strip().strip('[]'))
    parties = []
    for chunk in chunks:
        parts = [p.strip().strip('[]') for p in chunk.split('^')]
        valid = len(parts) >= 10 and bool(parts[3]) and len(_digits(parts[9])) == 10
        parties.append({'name': parts[3] if len(parts) > 3 else '',
                        'bizno': _digits(parts[9]) if len(parts) > 9 else '',
                        'share': parts[6] if len(parts) > 6 else '', 'valid': valid})
    return parties


@contextmanager
def _connection(existing=None):
    if existing is not None:
        yield existing
    else:
        with connect() as conn:
            ensure_vnext_schema(conn)
            yield conn


def _existing_notice_candidates(reference, _conn=None):
    """Resolve stored notice keys while tolerating numeric order zero-padding only."""
    ref = str(reference or '').strip()
    if not ref:
        return []
    with _connection(_conn) as conn:
        if '|' in ref:
            notice_no, _order = split_notice_key(ref)
            rows = conn.execute(
                """SELECT source_key FROM raw_records WHERE dataset=?
                   AND substr(source_key,1,instr(source_key,'|')-1)=?
                   ORDER BY id LIMIT 50""",
                (BID_DATASET, notice_no),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT source_key FROM raw_records WHERE dataset=?
                   AND (?=substr(source_key,1,instr(source_key,'|')-1)
                        OR ? LIKE substr(source_key,1,instr(source_key,'|')-1) || '%')
                   ORDER BY id LIMIT 50""",
                (BID_DATASET, ref, ref),
            ).fetchall()

    matches = set()
    for row in rows:
        key = str(row['source_key'])
        notice_no, notice_order = split_notice_key(key)
        if '|' in ref:
            if notice_keys_equivalent(key, ref):
                matches.add(key)
            continue
        if ref == notice_no:
            matches.add(key)
            continue
        if ref.startswith(notice_no):
            tail = ref[len(notice_no):]
            if tail and canonical_notice_order(tail) == canonical_notice_order(notice_order):
                matches.add(key)
    return sorted(matches)


def resolve_notice_key(row, _conn=None):
    no = str(_pick(row, 'bidNtceNo', 'bidNoticeNo')).strip()
    order = str(_pick(row, 'bidNtceOrd', 'bidNoticeOrd')).strip()
    ref = f'{no}|{order}' if no and order else no or str(_pick(row, 'ntceNo', 'noticeNo')).strip()
    candidates = _existing_notice_candidates(ref, _conn)
    return candidates[0] if len(candidates) == 1 else ''


def resolve_unique_final_award_key(notice, _conn=None):
    if '|' not in str(notice):
        return ''
    no, order = split_notice_key(notice)
    wanted_order = canonical_notice_order(order)
    with _connection(_conn) as conn:
        rows = conn.execute(
            """SELECT a.source_key,a.notice_order FROM award_results a
               JOIN raw_records r
                 ON r.dataset='award_result_service'
                AND r.source_key=a.final_award_raw_key
                AND r.payload_sha256=a.final_award_payload_sha256
               WHERE a.notice_no=?
                 AND (a.final_vendor<>'' OR a.final_vendor_bizno<>'' OR a.final_award_amount<>0)
               ORDER BY a.id LIMIT 20""",
            (no,),
        ).fetchall()
        rows = [
            row for row in rows
            if canonical_notice_order(row['notice_order']) == wanted_order
        ]

        # A new, not-yet-normalized final source must invalidate uniqueness too.
        raw = conn.execute(
            """SELECT payload_json FROM raw_records
               WHERE dataset='award_result_service'
                 AND json_extract(payload_json,'$.bidNtceNo')=?""",
            (no,),
        ).fetchall()
        from award_projection import execution_key
        raw_keys = set()
        for item in raw:
            payload = json.loads(item['payload_json'])
            raw_order = payload.get('bidNtceOrd', payload.get('bidNoticeOrd', '000'))
            if canonical_notice_order(raw_order) == wanted_order:
                raw_keys.add(execution_key(payload))
        keys = {row['source_key'] for row in rows}
        if len(raw_keys | keys) != 1 or len(keys) != 1:
            return ''
        return next(iter(keys))


def _dependency_token(conn):
    row = conn.execute('''SELECT COUNT(*) n,COALESCE(MAX(id),0) hi FROM raw_record_revisions
        WHERE dataset IN ('bid_notice_service','award_result_service')''').fetchone()
    return f"{row['n']}:{row['hi']}:{NORMALIZER_VERSION}"


def _link(conn, ft, fk, tt, tk, kind):
    conn.execute('''INSERT INTO lifecycle_links(from_type,from_key,to_type,to_key,link_type,confidence,reason)
        VALUES(?,?,?,?,?,1,'revision-aware contract projection')
        ON CONFLICT(from_type,from_key,to_type,to_key,link_type)
        DO UPDATE SET confidence=1,reason=excluded.reason''', (ft, fk, tt, tk, kind))


def _refresh_summary(conn, execution):
    if not execution:
        return
    records = conn.execute('SELECT * FROM vnext_contract_projection WHERE award_summary_key=?', (execution,)).fetchall()
    # Ambiguity is conservative even for direct/unverified projections; read views
    # additionally require a matching RAW digest before displaying any facts.
    current = []
    for rec in records:
        raw = conn.execute('SELECT payload_sha256 FROM raw_records WHERE dataset=? AND source_key=?',
                           (DATASET, rec['raw_source_key'])).fetchone()
        if raw and raw['payload_sha256'] != rec['payload_sha256']:
            continue
        if resolve_unique_final_award_key(rec['notice_key'], conn) == execution:
            current.append(rec)
    facts = dict(CONTRACT_DEFAULTS)
    raw_key = digest = ''
    if len(current) == 1:
        facts.update(json.loads(current[0]['facts_json']))
        raw_key, digest = current[0]['raw_source_key'], current[0]['payload_sha256']
    conn.execute('''UPDATE award_results SET contract_no=?,contract_vendor=?,contract_vendor_bizno=?,
        contract_amount=?,contract_raw_key=?,contract_payload_sha256=?,updated_at=CURRENT_TIMESTAMP
        WHERE source_key=?''', tuple(facts[k] for k in CONTRACT_DEFAULTS) + (raw_key, digest, execution))


def project_contract_row(row, *, raw_source_key=''):
    digest = _digest(row)
    key = str(raw_source_key or 'UNTRACKED:' + contract_key(row))
    parties = parse_contract_parties(_pick(row, 'corpList', 'companyList'))
    single = len(parties) == 1 and parties[0]['valid']
    number = contract_key(row)
    facts = dict(CONTRACT_DEFAULTS, contract_no=number,
                 contract_amount=_number(_pick(row, 'thtmCntrctAmt', 'totCntrctAmt', 'cntrctAmt')))
    if single:
        facts.update(contract_vendor=parties[0]['name'], contract_vendor_bizno=parties[0]['bizno'])
    with _connection() as conn:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        raw = conn.execute('SELECT payload_sha256 FROM raw_records WHERE dataset=? AND source_key=?',
                           (DATASET, key)).fetchone()
        if raw and raw['payload_sha256'] != digest:
            raise ValueError('RAW changed during contract projection')
        old = conn.execute('SELECT * FROM vnext_contract_projection WHERE raw_source_key=?', (key,)).fetchone()
        prior = conn.execute('SELECT source_key,contract_no FROM award_results WHERE contract_raw_key=?', (key,)).fetchall()
        previous_links = conn.execute('''SELECT to_key FROM lifecycle_links WHERE from_type=? AND from_key=?
            AND link_type='NORMALIZED_TO_CONTRACT' ''', (DATASET, key)).fetchall()
        old_numbers = {r['contract_no'] for r in prior} | {r['to_key'] for r in previous_links}
        if old:
            old_numbers.add(old['contract_no'])
        executions = {r['source_key'] for r in prior}
        if old:
            executions.add(old['award_summary_key'])
        # Invalidate first, even when the new notice/contract is unresolved.
        conn.execute('''UPDATE award_results SET contract_no='',contract_vendor='',contract_vendor_bizno='',
            contract_amount=0,contract_raw_key='',contract_payload_sha256='' WHERE contract_raw_key=?''', (key,))
        for old_no in old_numbers:
            conn.execute('''UPDATE lifecycle_links SET confidence=0,reason='superseded or ambiguous contract source'
                WHERE to_type='contract' AND to_key=?
                AND link_type IN ('HAS_CONTRACT','RESULTED_IN_CONTRACT','NORMALIZED_TO_CONTRACT')''', (old_no,))
        notice = resolve_notice_key(row, conn) if number else ''
        execution = resolve_unique_final_award_key(notice, conn) if notice else ''
        conn.execute('''INSERT INTO vnext_contract_projection(
            raw_source_key,contract_no,notice_key,award_summary_key,facts_json,payload_sha256,dependency_token,parties_valid)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(raw_source_key) DO UPDATE SET
            contract_no=excluded.contract_no,notice_key=excluded.notice_key,award_summary_key=excluded.award_summary_key,
            facts_json=excluded.facts_json,payload_sha256=excluded.payload_sha256,
            dependency_token=excluded.dependency_token,parties_valid=excluded.parties_valid,updated_at=CURRENT_TIMESTAMP''',
                     (key, number, notice, execution, json.dumps(facts, ensure_ascii=False, sort_keys=True),
                      digest, _dependency_token(conn), int(bool(parties) and all(p['valid'] for p in parties))))
        if notice:
            _link(conn, 'bid_notice', notice, 'contract', number, 'HAS_CONTRACT')
            _link(conn, DATASET, key, 'contract', number, 'NORMALIZED_TO_CONTRACT')
        if execution:
            _link(conn, 'award_summary', execution, 'contract', number, 'RESULTED_IN_CONTRACT')
            executions.add(execution)
        for target in executions:
            _refresh_summary(conn, target)
        _reconcile_assignments(conn)
    reason = 'missing_contract_key' if not number else 'notice_unresolved' if not notice else ''
    return dict(projected=bool(notice), linked=bool(notice), reason=reason,
                notice_key=notice, award_summary_key=execution, award_summary_linked=bool(execution),
                contract_no=number, party_count=len(parties), single_party_projected=single)


def _reconcile_assignments(conn):
    stale = 0
    records = conn.execute("SELECT raw_source_key,notice_key,award_summary_key,contract_no FROM vnext_contract_projection WHERE award_summary_key<>''").fetchall()
    for rec in records:
        if resolve_unique_final_award_key(rec['notice_key'], conn) != rec['award_summary_key']:
            conn.execute("UPDATE vnext_contract_projection SET award_summary_key='' WHERE raw_source_key=?", (rec['raw_source_key'],))
            conn.execute("UPDATE lifecycle_links SET confidence=0,reason='final execution ambiguous or stale' WHERE from_type='award_summary' AND from_key=? AND to_type='contract' AND to_key=?", (rec['award_summary_key'], rec['contract_no']))
            _refresh_summary(conn, rec['award_summary_key'])
            stale += 1
    return stale


def reconcile_contract_assignments():
    """Called on final-award changes, without refetching unchanged contracts."""
    with _connection() as conn:
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        return _reconcile_assignments(conn)


def normalize_contracts(*, limit=None):
    with _connection() as conn:
        token = _dependency_token(conn)
        sql = '''SELECT r.id,r.source_key,r.payload_json,r.payload_sha256 FROM raw_records r
            LEFT JOIN vnext_contract_projection p ON p.raw_source_key=r.source_key
            WHERE r.dataset=? AND (r.normalized_at='' OR r.normalizer_version<>?
              OR p.raw_source_key IS NULL OR p.dependency_token<>? OR p.payload_sha256<>r.payload_sha256)
            ORDER BY r.id'''
        args = [DATASET, NORMALIZER_VERSION, token]
        if limit is not None:
            sql += ' LIMIT ?'
            args.append(max(0, int(limit)))
        records = [dict(r) for r in conn.execute(sql, args).fetchall()]
    processed = linked = assigned = unresolved = 0
    errors = []
    for raw in records:
        try:
            outcome = project_contract_row(json.loads(raw['payload_json']), raw_source_key=raw['source_key'])
            processed += 1
            linked += int(outcome['linked'])
            assigned += int(outcome['award_summary_linked'])
            unresolved += int(outcome['reason'] == 'notice_unresolved')
            with connect() as conn:
                conn.execute('UPDATE raw_records SET normalized_at=CURRENT_TIMESTAMP,normalizer_version=? WHERE id=? AND payload_sha256=?',
                             (NORMALIZER_VERSION, raw['id'], raw['payload_sha256']))
        except Exception as exc:
            errors.append({'raw_id': raw['id'], 'error': type(exc).__name__})
    with _connection() as conn:
        pending = conn.execute("""SELECT COUNT(*) n FROM raw_records r LEFT JOIN vnext_contract_projection p ON p.raw_source_key=r.source_key
            WHERE r.dataset=? AND (r.normalized_at='' OR r.normalizer_version<>? OR p.raw_source_key IS NULL OR p.dependency_token<>? OR p.payload_sha256<>r.payload_sha256)""",
            (DATASET, NORMALIZER_VERSION, _dependency_token(conn))).fetchone()['n']
    return dict(dataset=DATASET, processed=processed, linked=linked, pending=pending,
                complete=not errors and pending == 0,
                award_summary_linked=assigned, unresolved=unresolved, errors=errors)
