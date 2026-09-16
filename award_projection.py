"""Normalize G2B service opening/award RAW records after collection.

Collection never filters or discards rows. This module is a later projection layer:
- opening_result_service -> conservative first-rank facts
- award_result_service   -> official final-award facts

Normalized facts are keyed by the official execution/rebid identity so multiple
executions under one notice are never silently merged. Each normalized fact group
keeps RAW provenance and is replaced exactly when that RAW record changes.
"""
import json

from db import connect
from projection_store_vnext import replace_fact_group, clear_fact_group_by_raw
from vnext_schema import ensure_vnext_schema, NORMALIZER_VERSION
from vnext_store import save_lifecycle_link, upsert_award_result

OPENING_DATASET = "opening_result_service"
AWARD_DATASET = "award_result_service"


def _pick(row, *names, default=""):
    for name in names:
        value = row.get(name) if isinstance(row, dict) else None
        if value not in (None, ""):
            return value
    return default


def _number(value, default=0.0):
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return default


def _integer(value):
    return int(round(_number(value, 0)))


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _date(value):
    digits = _digits(value)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(value or "")[:10]


def notice_key(row):
    notice_no = str(_pick(row, "bidNtceNo", "bidNoticeNo")).strip()
    notice_order = str(_pick(row, "bidNtceOrd", "bidNoticeOrd", default="000")).strip() or "000"
    return f"{notice_no}|{notice_order}" if notice_no else ""


def execution_key(row):
    """Stable normalized award identity: notice/order + execution + rebid."""
    notice = notice_key(row)
    if not notice:
        return ""
    bid_clsfc = str(_pick(row, "bidClsfcNo", "bidClsfNo", default="0")).strip() or "0"
    rebid = str(_pick(row, "rbidNo", "rebidNo", default="0")).strip() or "0"
    return f"{notice}|{bid_clsfc}|{rebid}"


def parse_opening_corp_info(value):
    """Parse only unambiguous single-company price-opening summaries."""
    text = str(value or "").strip()
    result = {"case": "empty", "vendor": "", "bizno": "", "amount": 0, "rate": 0.0}
    if not text:
        return result
    parts = [part.strip() for part in text.split("^")]
    if parts and parts[0].startswith("낙찰예정자 다수"):
        result["case"] = "multiple"
        return result
    if len(parts) < 5:
        result["case"] = "malformed"
        return result
    amount = _integer(parts[3])
    rate = _number(parts[4], 0.0)
    if not amount or not rate:
        result["case"] = "negotiation"
        return result
    return {
        "case": "single",
        "vendor": parts[0],
        "bizno": _digits(parts[1]),
        "amount": amount,
        "rate": rate,
    }


def _link_notice_to_execution(notice, execution):
    save_lifecycle_link(
        "bid_notice", notice, "award_summary", execution, "HAS_AWARD_EXECUTION",
        confidence=1.0, reason="official notice/order + bidClsfcNo + rbidNo identity",
    )


def project_opening_row(row, *, raw_source_key=""):
    """Replace opening/first-rank facts for one official execution."""
    notice = notice_key(row)
    execution = execution_key(row)
    if not notice or not execution:
        clear_fact_group_by_raw("opening", raw_source_key)
        return {"projected": False, "reason": "missing_execution_key"}
    notice_no, notice_order = notice.split("|", 1)
    progress = str(_pick(row, "progrsDivCdNm", "progressName")).strip()
    corp = parse_opening_corp_info(_pick(row, "opengCorpInfo", "openingCorpInfo"))
    opening_date = _date(_pick(row, "opengDt", "rlOpengDt", "opengDate"))
    participant_count = _integer(_pick(row, "prtcptCnum", "participantCount"))
    first_rank = progress == "개찰완료" and corp["case"] == "single"

    base = {"notice_no": notice_no, "notice_order": notice_order, "business_type": "용역"}
    replace_fact_group(
        execution,
        "opening",
        raw_source_key,
        base_facts=base,
        source_payload=row,
        opening_date=opening_date,
        participant_count=participant_count,
        first_rank_vendor=(corp["vendor"] if first_rank else ""),
        first_rank_bizno=(corp["bizno"] if first_rank else ""),
        first_rank_amount=(corp["amount"] if first_rank else 0),
    )
    _link_notice_to_execution(notice, execution)
    return {
        "projected": True,
        "first_rank_projected": first_rank,
        "opening_case": corp["case"],
        "progress": progress,
        "notice_key": notice,
        "award_summary_key": execution,
    }


def project_final_award_row(row, *, raw_source_key=""):
    """Replace official final-award facts without substituting opening rank data."""
    notice = notice_key(row)
    execution = execution_key(row)
    if not notice or not execution:
        clear_fact_group_by_raw("final_award", raw_source_key)
        from contract_projection import reconcile_contract_assignments
        reconcile_contract_assignments()
        return {"projected": False, "reason": "missing_execution_key"}
    notice_no, notice_order = notice.split("|", 1)
    vendor = str(_pick(row, "bidwinnrNm", "fnlSucsfCorpNm")).strip()
    bizno = _digits(_pick(row, "bidwinnrBizno", "fnlSucsfCorpBizno"))
    amount = _integer(_pick(row, "sucsfbidAmt", "finalAwardAmount"))
    rate = _number(_pick(row, "sucsfbidRate", "finalAwardRate"), 0.0)
    base = {
        "notice_no": notice_no,
        "notice_order": notice_order,
        "business_type": "용역",
    }
    replace_fact_group(
        execution,
        "final_award",
        raw_source_key,
        base_facts=base,
        source_payload=row,
        final_vendor=vendor,
        final_vendor_bizno=bizno,
        final_award_amount=amount,
        award_rate=rate,
    )
    _link_notice_to_execution(notice, execution)
    from contract_projection import reconcile_contract_assignments
    reconcile_contract_assignments()
    return {
        "projected": True,
        "final_award_projected": bool(vendor or bizno or amount),
        "notice_key": notice,
        "award_summary_key": execution,
    }


def normalize_dataset(dataset, *, limit=None):
    """Re-runnable RAW -> award_results projection for one supported dataset."""
    if dataset not in (OPENING_DATASET, AWARD_DATASET):
        raise ValueError("unsupported award projection dataset")
    sql = "SELECT id,source_key,payload_json,payload_sha256 FROM raw_records WHERE dataset=? AND (normalized_at='' OR normalizer_version<>?) ORDER BY id"
    params = [dataset, NORMALIZER_VERSION]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(0, int(limit)))
    with connect() as conn:
        ensure_vnext_schema(conn)
        rows = [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]

    processed = projected = first_rank = final_award = 0
    errors = []
    projector = project_opening_row if dataset == OPENING_DATASET else project_final_award_row
    for raw in rows:
        try:
            payload = json.loads(raw["payload_json"])
            outcome = projector(payload, raw_source_key=raw["source_key"])
            processed += 1
            projected += int(bool(outcome.get("projected")))
            first_rank += int(bool(outcome.get("first_rank_projected")))
            final_award += int(bool(outcome.get("final_award_projected")))
            summary_key = outcome.get("award_summary_key")
            if summary_key:
                save_lifecycle_link(
                    dataset, raw["source_key"], "award_summary", summary_key,
                    "NORMALIZED_TO_AWARD_SUMMARY", confidence=1.0,
                    reason="post-RAW deterministic execution normalization",
                )
            with connect() as conn:
                conn.execute("UPDATE raw_records SET normalized_at=CURRENT_TIMESTAMP,normalizer_version=? WHERE id=? AND payload_sha256=?",
                             (NORMALIZER_VERSION, raw["id"], raw["payload_sha256"]))
        except Exception as exc:
            errors.append({"raw_id": raw["id"], "error": str(exc)[:500]})
    with connect() as conn:
        pending = conn.execute("SELECT COUNT(*) n FROM raw_records WHERE dataset=? AND (normalized_at='' OR normalizer_version<>?)", (dataset, NORMALIZER_VERSION)).fetchone()['n']
    return {
        "dataset": dataset,
        "pending": pending,
        "complete": not errors and pending == 0,
        "processed": processed,
        "projected": projected,
        "first_rank": first_rank,
        "final_award": final_award,
        "errors": errors,
    }


def normalize_service_awards(*, limit=None):
    return {
        "opening": normalize_dataset(OPENING_DATASET, limit=limit),
        "award": normalize_dataset(AWARD_DATASET, limit=limit),
    }
