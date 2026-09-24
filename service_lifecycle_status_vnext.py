"""Read-only organization diagnostics for the stored G2B service lifecycle.

This module answers a narrow question: given RAW already present in vNext storage,
how much of it is currently organized into notice -> opening -> final award ->
contract relations?

It deliberately does NOT claim that the official source itself was fully collected.
"""
from __future__ import annotations

from db import connect
from vnext_schema import NORMALIZER_VERSION, ensure_vnext_schema
import contract_projection

SERVICE_DATASETS = (
    "bid_notice_service",
    "opening_result_service",
    "award_result_service",
    "contract_service",
)


def _scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _keys(conn, sql, params=(), limit=100):
    rows = conn.execute(sql + " LIMIT ?", (*params, max(1, int(limit)))).fetchall()
    return [str(row[0]) for row in rows]


def service_lifecycle_organization_status(*, orphan_limit=100):
    """Return CURRENT_STORED_RAW_ONLY organization diagnostics.

    Counts are about local organization/projection state only. They are not source
    collection completeness proof and do not perform source traffic.
    """
    with connect() as conn:
        ensure_vnext_schema(conn)

        raw_counts = {
            dataset: _scalar(
                conn,
                "SELECT COUNT(*) FROM raw_records WHERE dataset=?",
                (dataset,),
            )
            for dataset in SERVICE_DATASETS
        }

        normalization_pending = {
            dataset: _scalar(
                conn,
                """SELECT COUNT(*) FROM raw_records
                   WHERE dataset=?
                     AND (normalized_at='' OR normalizer_version<>?)""",
                (dataset, NORMALIZER_VERSION),
            )
            for dataset in ("opening_result_service", "award_result_service")
        }
        dependency_token = contract_projection._dependency_token(conn)
        normalization_pending["contract_service"] = _scalar(
            conn,
            """SELECT COUNT(*)
               FROM raw_records r
               LEFT JOIN vnext_contract_projection p
                 ON p.raw_source_key=r.source_key
               WHERE r.dataset='contract_service'
                 AND (
                    r.normalized_at='' OR r.normalizer_version<>?
                    OR p.raw_source_key IS NULL
                    OR p.dependency_token<>?
                    OR p.payload_sha256<>r.payload_sha256
                 )""",
            (NORMALIZER_VERSION, dependency_token),
        )

        current_opening_projection = _scalar(
            conn,
            """SELECT COUNT(*)
               FROM award_results a
               JOIN raw_records r
                 ON r.dataset='opening_result_service'
                AND r.source_key=a.opening_raw_key
                AND r.payload_sha256=a.opening_payload_sha256
               WHERE a.opening_raw_key<>''""",
        )
        current_first_rank = _scalar(
            conn,
            """SELECT COUNT(*)
               FROM award_results a
               JOIN raw_records r
                 ON r.dataset='opening_result_service'
                AND r.source_key=a.opening_raw_key
                AND r.payload_sha256=a.opening_payload_sha256
               WHERE a.opening_raw_key<>''
                 AND (a.first_rank_vendor<>'' OR a.first_rank_bizno<>'' OR a.first_rank_amount<>0)""",
        )
        current_final_award_projection = _scalar(
            conn,
            """SELECT COUNT(*)
               FROM award_results a
               JOIN raw_records r
                 ON r.dataset='award_result_service'
                AND r.source_key=a.final_award_raw_key
                AND r.payload_sha256=a.final_award_payload_sha256
               WHERE a.final_award_raw_key<>''""",
        )
        current_final_vendor = _scalar(
            conn,
            """SELECT COUNT(*)
               FROM award_results a
               JOIN raw_records r
                 ON r.dataset='award_result_service'
                AND r.source_key=a.final_award_raw_key
                AND r.payload_sha256=a.final_award_payload_sha256
               WHERE a.final_award_raw_key<>''
                 AND (a.final_vendor<>'' OR a.final_vendor_bizno<>'' OR a.final_award_amount<>0)""",
        )

        current_contract_projection = _scalar(
            conn,
            """SELECT COUNT(*)
               FROM vnext_contract_projection p
               JOIN raw_records r
                 ON r.dataset='contract_service'
                AND r.source_key=p.raw_source_key
                AND r.payload_sha256=p.payload_sha256""",
        )
        contracts_with_notice = _scalar(
            conn,
            """SELECT COUNT(*)
               FROM vnext_contract_projection p
               JOIN raw_records r
                 ON r.dataset='contract_service'
                AND r.source_key=p.raw_source_key
                AND r.payload_sha256=p.payload_sha256
               WHERE p.notice_key<>''""",
        )
        contracts_assigned_to_final_award = _scalar(
            conn,
            """SELECT COUNT(*)
               FROM vnext_contract_projection p
               JOIN raw_records r
                 ON r.dataset='contract_service'
                AND r.source_key=p.raw_source_key
                AND r.payload_sha256=p.payload_sha256
               WHERE p.notice_key<>'' AND p.award_summary_key<>''""",
        )

        notices_with_execution = _scalar(
            conn,
            """SELECT COUNT(DISTINCT l.from_key)
               FROM lifecycle_links l
               JOIN raw_records n
                 ON n.dataset='bid_notice_service' AND n.source_key=l.from_key
               JOIN award_results a ON a.source_key=l.to_key
               WHERE l.from_type='bid_notice'
                 AND l.to_type='award_summary'
                 AND l.link_type='HAS_AWARD_EXECUTION'
                 AND l.confidence>0""",
        )
        notices_with_final_award = _scalar(
            conn,
            """SELECT COUNT(DISTINCT l.from_key)
               FROM lifecycle_links l
               JOIN raw_records n
                 ON n.dataset='bid_notice_service' AND n.source_key=l.from_key
               JOIN award_results a ON a.source_key=l.to_key
               JOIN raw_records rf
                 ON rf.dataset='award_result_service'
                AND rf.source_key=a.final_award_raw_key
                AND rf.payload_sha256=a.final_award_payload_sha256
               WHERE l.from_type='bid_notice'
                 AND l.to_type='award_summary'
                 AND l.link_type='HAS_AWARD_EXECUTION'
                 AND l.confidence>0
                 AND (a.final_vendor<>'' OR a.final_vendor_bizno<>'' OR a.final_award_amount<>0)""",
        )
        notices_with_contract = _scalar(
            conn,
            """SELECT COUNT(DISTINCT l.from_key)
               FROM lifecycle_links l
               JOIN raw_records n
                 ON n.dataset='bid_notice_service' AND n.source_key=l.from_key
               JOIN vnext_contract_projection p ON p.contract_no=l.to_key
               JOIN raw_records rc
                 ON rc.dataset='contract_service'
                AND rc.source_key=p.raw_source_key
                AND rc.payload_sha256=p.payload_sha256
               WHERE l.from_type='bid_notice'
                 AND l.to_type='contract'
                 AND l.link_type='HAS_CONTRACT'
                 AND l.confidence>0""",
        )

        final_award_without_notice_link = _keys(
            conn,
            """SELECT a.source_key
               FROM award_results a
               JOIN raw_records rf
                 ON rf.dataset='award_result_service'
                AND rf.source_key=a.final_award_raw_key
                AND rf.payload_sha256=a.final_award_payload_sha256
               WHERE (a.final_vendor<>'' OR a.final_vendor_bizno<>'' OR a.final_award_amount<>0)
                 AND NOT EXISTS (
                    SELECT 1 FROM lifecycle_links l
                    JOIN raw_records n
                      ON n.dataset='bid_notice_service' AND n.source_key=l.from_key
                    WHERE l.from_type='bid_notice'
                      AND l.to_type='award_summary'
                      AND l.to_key=a.source_key
                      AND l.link_type='HAS_AWARD_EXECUTION'
                      AND l.confidence>0
                 )
               ORDER BY a.source_key""",
            limit=orphan_limit,
        )
        contract_notice_unresolved = _keys(
            conn,
            """SELECT p.raw_source_key
               FROM vnext_contract_projection p
               JOIN raw_records r
                 ON r.dataset='contract_service'
                AND r.source_key=p.raw_source_key
                AND r.payload_sha256=p.payload_sha256
               WHERE p.notice_key=''
               ORDER BY p.raw_source_key""",
            limit=orphan_limit,
        )
        contract_final_award_unassigned = _keys(
            conn,
            """SELECT p.raw_source_key
               FROM vnext_contract_projection p
               JOIN raw_records r
                 ON r.dataset='contract_service'
                AND r.source_key=p.raw_source_key
                AND r.payload_sha256=p.payload_sha256
               WHERE p.notice_key<>'' AND p.award_summary_key=''
               ORDER BY p.raw_source_key""",
            limit=orphan_limit,
        )

    return {
        "scope": "CURRENT_STORED_RAW_ONLY",
        "normalizer_version": NORMALIZER_VERSION,
        "raw_counts": raw_counts,
        "normalization_pending": normalization_pending,
        "projected": {
            "opening_executions": current_opening_projection,
            "first_rank_executions": current_first_rank,
            "final_award_executions": current_final_award_projection,
            "final_award_with_vendor_or_amount": current_final_vendor,
            "contracts": current_contract_projection,
            "contracts_with_notice": contracts_with_notice,
            "contracts_assigned_to_unique_final_award": contracts_assigned_to_final_award,
        },
        "notice_organization": {
            "notices_with_award_execution": notices_with_execution,
            "notices_with_final_award": notices_with_final_award,
            "notices_with_contract": notices_with_contract,
        },
        "needs_review": {
            "final_award_without_notice_link": final_award_without_notice_link,
            "contract_notice_unresolved": contract_notice_unresolved,
            "contract_final_award_unassigned": contract_final_award_unassigned,
        },
        "read_only": True,
        "source_traffic": False,
        "source_collection_completeness_verified": False,
        "source_collection_completeness_reason": "NOT_EVALUATED_BY_LOCAL_ORGANIZATION_STATUS",
    }
