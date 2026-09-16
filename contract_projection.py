"""Post-RAW service-contract normalization and lifecycle linking.

Notice references from contract data are not sliced heuristically. A contract is
linked to the bid notice only when its reference matches one exact vNext notice.
Contract facts are merged into an award execution only when exactly one official
final-award execution exists for that notice; ambiguous executions stay separate.
"""
import json

from db import connect
from vnext_schema import ensure_vnext_schema
from vnext_store import save_lifecycle_link, upsert_award_result

DATASET = "contract_service"
BID_DATASET = "bid_notice_service"


def _pick(row, *names, default=""):
    for name in names:
        value = row.get(name) if isinstance(row, dict) else None
        if value not in (None, ""):
            return value
    return default


def _number(value):
    try:
        return int(round(float(str(value).replace(",", "").strip())))
    except Exception:
        return 0


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def contract_key(row):
    return str(_pick(row, "dcsnCntrctNo", "untyCntrctNo", "contractNo")).strip()


def parse_contract_parties(value):
    """Parse the documented caret-delimited corpList while preserving ambiguity."""
    text = str(value or "").strip()
    if not text:
        return []
    text = text.strip().strip("[]")
    chunks = text.replace("],[", "\n").replace("], [", "\n").splitlines()
    parties = []
    for chunk in chunks:
        parts = [part.strip().strip("[]") for part in chunk.split("^")]
        if len(parts) < 4:
            continue
        parties.append({
            "name": parts[3] if len(parts) > 3 else "",
            "bizno": _digits(parts[9]) if len(parts) > 9 else "",
            "share": parts[6] if len(parts) > 6 else "",
        })
    return parties


def _existing_notice_candidates(reference):
    ref = str(reference or "").strip()
    if not ref:
        return []
    with connect() as conn:
        ensure_vnext_schema(conn)
        found = set()
        if "|" in ref:
            row = conn.execute(
                "SELECT source_key FROM raw_records WHERE dataset=? AND source_key=? LIMIT 1",
                (BID_DATASET, ref),
            ).fetchone()
            if row:
                found.add(row["source_key"])
        for row in conn.execute(
            "SELECT source_key FROM raw_records WHERE dataset=? AND REPLACE(source_key,'|','')=? LIMIT 3",
            (BID_DATASET, ref),
        ).fetchall():
            found.add(row["source_key"])
        for row in conn.execute(
            "SELECT source_key FROM raw_records WHERE dataset=? AND source_key LIKE ? LIMIT 3",
            (BID_DATASET, ref + "|%"),
        ).fetchall():
            found.add(row["source_key"])
        return sorted(found)


def resolve_notice_key(row):
    """Return one exact known notice identity or blank when missing/ambiguous."""
    explicit_no = str(_pick(row, "bidNtceNo", "bidNoticeNo")).strip()
    explicit_order = str(_pick(row, "bidNtceOrd", "bidNoticeOrd")).strip()
    if explicit_no and explicit_order:
        candidate = f"{explicit_no}|{explicit_order}"
        matches = _existing_notice_candidates(candidate)
        return candidate if candidate in matches else ""
    if explicit_no:
        matches = _existing_notice_candidates(explicit_no)
        return matches[0] if len(matches) == 1 else ""
    combined = str(_pick(row, "ntceNo", "noticeNo")).strip()
    matches = _existing_notice_candidates(combined)
    return matches[0] if len(matches) == 1 else ""


def resolve_unique_final_award_key(notice):
    """Return one final-award execution key or blank when none/ambiguous."""
    text = str(notice or "")
    if "|" not in text:
        return ""
    notice_no, notice_order = text.split("|", 1)
    with connect() as conn:
        ensure_vnext_schema(conn)
        rows = conn.execute(
            """SELECT source_key FROM award_results
               WHERE notice_no=? AND notice_order=?
                 AND (final_vendor<>'' OR final_vendor_bizno<>'' OR final_award_amount<>0)
               ORDER BY id LIMIT 3""",
            (notice_no, notice_order),
        ).fetchall()
    keys = [str(row["source_key"]) for row in rows if str(row["source_key"] or "")]
    return keys[0] if len(keys) == 1 else ""


def project_contract_row(row, *, raw_source_key=""):
    notice = resolve_notice_key(row)
    contract_no = contract_key(row)
    if not contract_no:
        return {"projected": False, "linked": False, "reason": "missing_contract_key"}
    if not notice:
        return {"projected": False, "linked": False, "reason": "notice_unresolved", "contract_no": contract_no}

    parties = parse_contract_parties(_pick(row, "corpList", "companyList"))
    contract_amount = _number(_pick(row, "thtmCntrctAmt", "totCntrctAmt", "cntrctAmt"))
    facts = {
        "contract_no": contract_no,
        "contract_amount": contract_amount,
    }
    if len(parties) == 1:
        facts["contract_vendor"] = parties[0]["name"]
        facts["contract_vendor_bizno"] = parties[0]["bizno"]

    award_summary_key = resolve_unique_final_award_key(notice)
    award_summary_linked = bool(award_summary_key)
    if award_summary_key:
        upsert_award_result(award_summary_key, **facts)

    save_lifecycle_link(
        "bid_notice", notice, "contract", contract_no, "HAS_CONTRACT",
        confidence=1.0, reason="contract notice reference matched one exact vNext bid identity",
    )
    if award_summary_key:
        save_lifecycle_link(
            "award_summary", award_summary_key, "contract", contract_no, "RESULTED_IN_CONTRACT",
            confidence=1.0, reason="unique official final-award execution for exact notice identity",
        )
    if raw_source_key:
        save_lifecycle_link(
            DATASET, raw_source_key, "contract", contract_no, "NORMALIZED_TO_CONTRACT",
            confidence=1.0, reason="post-RAW deterministic normalization",
        )
    return {
        "projected": True,
        "linked": True,
        "notice_key": notice,
        "award_summary_key": award_summary_key,
        "award_summary_linked": award_summary_linked,
        "contract_no": contract_no,
        "party_count": len(parties),
        "single_party_projected": len(parties) == 1,
    }


def normalize_contracts(*, limit=None):
    sql = "SELECT id,source_key,payload_json FROM raw_records WHERE dataset=? ORDER BY id"
    params = [DATASET]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(0, int(limit)))
    with connect() as conn:
        ensure_vnext_schema(conn)
        rows = [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]

    processed = linked = award_summary_linked = unresolved = 0
    errors = []
    for raw in rows:
        try:
            payload = json.loads(raw["payload_json"])
            outcome = project_contract_row(payload, raw_source_key=raw["source_key"])
            processed += 1
            linked += int(bool(outcome.get("linked")))
            award_summary_linked += int(bool(outcome.get("award_summary_linked")))
            unresolved += int(outcome.get("reason") == "notice_unresolved")
            with connect() as conn:
                conn.execute("UPDATE raw_records SET normalized_at=CURRENT_TIMESTAMP WHERE id=?", (raw["id"],))
        except Exception as exc:
            errors.append({"raw_id": raw["id"], "error": str(exc)[:500]})
    return {
        "dataset": DATASET,
        "processed": processed,
        "linked": linked,
        "award_summary_linked": award_summary_linked,
        "unresolved": unresolved,
        "errors": errors,
    }
