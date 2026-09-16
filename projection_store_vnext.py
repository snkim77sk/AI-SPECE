"""Replaceable normalized fact groups with RAW provenance for G2B vNext.

RAW remains immutable history. This module owns only derived projection state, so a
new revision can replace or clear stale first-rank/final-award/contract facts without
destroying source history.
"""
from __future__ import annotations

import hashlib
import json

from db import connect
from vnext_schema import ensure_vnext_schema
from vnext_store import upsert_award_result

_GROUPS = {
    "opening": {
        "raw_column": "opening_raw_key",
        "fields": {
            "opening_date": "",
            "participant_count": 0,
            "first_rank_vendor": "",
            "first_rank_bizno": "",
            "first_rank_amount": 0,
        },
    },
    "final_award": {
        "raw_column": "final_award_raw_key",
        "fields": {
            "final_vendor": "",
            "final_vendor_bizno": "",
            "final_award_amount": 0,
            "award_rate": 0.0,
        },
    },
    "contract": {
        "raw_column": "contract_raw_key",
        "fields": {
            "contract_no": "",
            "contract_vendor": "",
            "contract_vendor_bizno": "",
            "contract_amount": 0,
        },
    },
}


def _spec(group):
    value = _GROUPS.get(str(group or ""))
    if not value:
        raise ValueError("group must be opening, final_award, or contract")
    return value


def clear_fact_group_by_raw(group, raw_source_key):
    """Clear derived facts previously produced by one logical RAW source."""
    raw_key = str(raw_source_key or "")
    if not raw_key:
        return 0
    spec = _spec(group)
    fields = list(spec["fields"])
    assignments = ",".join([f"{field}=?" for field in fields] + [f"{spec['raw_column']}=''", f"{group}_payload_sha256=''"])
    values = [spec["fields"][field] for field in fields]
    with connect() as conn:
        ensure_vnext_schema(conn)
        cur = conn.execute(
            f"UPDATE award_results SET {assignments},updated_at=CURRENT_TIMESTAMP "
            f"WHERE {spec['raw_column']}=?",
            (*values, raw_key),
        )
        return int(cur.rowcount or 0)


def replace_fact_group(source_key, group, raw_source_key="", *, base_facts=None, source_payload=None, **values):
    """Replace a source-owned fact group exactly.

    Omitted group fields are reset to defaults. If the same RAW source was previously
    attached to another execution, its old group is cleared before reassignment.
    """
    spec = _spec(group)
    unknown = set(values) - set(spec["fields"])
    if unknown:
        raise ValueError("unknown fact-group fields: " + ", ".join(sorted(unknown)))
    upsert_award_result(source_key, **(base_facts or {}))
    raw_key = str(raw_source_key or "")
    exact = dict(spec["fields"])
    exact.update(values)
    fields = list(spec["fields"])
    dataset = {"opening": "opening_result_service", "final_award": "award_result_service", "contract": "contract_service"}[group]
    with connect() as conn:
        ensure_vnext_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        digest = ""
        raw = conn.execute("SELECT payload_sha256 FROM raw_records WHERE dataset=? AND source_key=?",
                           (dataset, raw_key)).fetchone() if raw_key else None
        if source_payload is not None:
            digest = hashlib.sha256(json.dumps(source_payload, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")).encode()).hexdigest()
            if raw and raw["payload_sha256"] != digest:
                raise ValueError("RAW changed during projection; retry current revision")
        elif raw:
            digest = raw["payload_sha256"]
        if raw_key:
            clear_assignments = ",".join([f"{field}=?" for field in fields] + [f"{spec['raw_column']}=''", f"{group}_payload_sha256=''"])
            conn.execute(
                f"UPDATE award_results SET {clear_assignments},updated_at=CURRENT_TIMESTAMP "
                f"WHERE {spec['raw_column']}=? AND source_key<>?",
                (*[spec["fields"][field] for field in fields], raw_key, source_key),
            )
        assignments = ",".join([f"{field}=?" for field in fields] + [f"{spec['raw_column']}=?"])
        conn.execute(
            f"UPDATE award_results SET {assignments},{group}_payload_sha256=?,updated_at=CURRENT_TIMESTAMP WHERE source_key=?",
            (*[exact[field] for field in fields], raw_key, digest, source_key),
        )
    return exact


def retire_contract_links(contract_no):
    """Retire previous derived contract links while retaining their audit history."""
    key = str(contract_no or "")
    if not key:
        return 0
    with connect() as conn:
        ensure_vnext_schema(conn)
        cur = conn.execute(
            """UPDATE lifecycle_links
               SET confidence=0,reason='superseded by contract RAW revision'
               WHERE to_type='contract' AND to_key=?
                 AND link_type IN ('HAS_CONTRACT','RESULTED_IN_CONTRACT','NORMALIZED_TO_CONTRACT')
                 AND confidence<>0""",
            (key,),
        )
        return int(cur.rowcount or 0)
