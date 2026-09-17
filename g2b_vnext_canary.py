"""Sanitized live canary for the independent G2B vNext ingestion paths.

The probe never writes source rows to production tables and never prints raw vendor,
business-number, contract-number, delivery-number, or notice-number values. It
reports only field presence and structural statistics needed to validate parsers
before backfill.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from zoneinfo import ZoneInfo

import award_vnext
import bid_vnext
import contract_vnext
import db
import shopping_vnext
from award_projection import parse_opening_corp_info
from contract_projection import parse_contract_parties

KST = ZoneInfo("Asia/Seoul")
DEFAULT_ROWS = 10
DEFAULT_LOOKBACK_DAYS = 1
CANARY_DATASETS = {
    "goods_notice": "bid_notice_goods",
    "service_notice": "bid_notice_service",
    "service_opening": "opening_result_service",
    "service_final_award": "award_result_service",
    "service_contract": "contract_service",
    "shopping_delivery": "shopping_delivery",
}


def _nonempty(value):
    return value not in (None, "", [], {})


def _field_stats(rows, fields):
    total = len(rows)
    out = {}
    for field in fields:
        present = sum(1 for row in rows if field in row)
        nonempty = sum(1 for row in rows if _nonempty(row.get(field)))
        out[field] = {"present": present, "nonempty": nonempty, "rows": total}
    return out


def _opening_shape(rows):
    cases = Counter()
    token_counts = Counter()
    completed = 0
    first_rank_candidates = 0
    for row in rows:
        value = str(row.get("opengCorpInfo") or "")
        if value:
            token_counts[len(value.split("^"))] += 1
        parsed = parse_opening_corp_info(value)
        cases[parsed["case"]] += 1
        if str(row.get("progrsDivCdNm") or "").strip() == "개찰완료":
            completed += 1
            if parsed["case"] == "single":
                first_rank_candidates += 1
    return {
        "opengCorpInfo_token_counts": dict(sorted(token_counts.items())),
        "parser_cases": dict(sorted(cases.items())),
        "completed_rows": completed,
        "conservative_first_rank_candidates": first_rank_candidates,
    }


def _award_shape(rows):
    return {
        "rows_with_any_final_award_fact": sum(
            1 for row in rows
            if any(_nonempty(row.get(k)) for k in ("bidwinnrNm", "bidwinnrBizno", "sucsfbidAmt"))
        )
    }


def _contract_shape(rows):
    ntce_lengths = Counter()
    party_counts = Counter()
    corp_token_counts = Counter()
    parseable = 0
    for row in rows:
        ntce = str(row.get("ntceNo") or "").strip()
        if ntce:
            ntce_lengths[len(ntce)] += 1
        corp = str(row.get("corpList") or "").strip()
        if corp:
            chunks = corp.strip().strip("[]").replace("],[", "\n").replace("], [", "\n").splitlines()
            for chunk in chunks:
                corp_token_counts[len(chunk.split("^"))] += 1
        parties = parse_contract_parties(corp)
        party_counts[len(parties)] += 1
        if parties and all(p.get("valid") for p in parties):
            parseable += 1
    return {
        "ntceNo_length_counts": dict(sorted(ntce_lengths.items())),
        "corpList_entry_token_counts": dict(sorted(corp_token_counts.items())),
        "parsed_party_count_distribution": dict(sorted(party_counts.items())),
        "rows_with_parseable_parties": parseable,
    }


def summarize_rows(rows, fields, shape_fn=None):
    keys = sorted({str(key) for row in rows for key in row.keys()})
    result = {
        "page_rows": len(rows),
        "keys": keys,
        "field_stats": _field_stats(rows, fields),
    }
    if shape_fn:
        result["shape"] = shape_fn(rows)
    return result


def _probe_one_day(fetcher, fields, shape_fn=None, *, today=None, rows=DEFAULT_ROWS,
                   lookback_days=DEFAULT_LOOKBACK_DAYS):
    today = today or dt.datetime.now(KST).date()
    attempts = []
    selected_items = []
    selected_total = 0
    selected_day = ""
    for offset in range(max(1, int(lookback_days))):
        day = today - dt.timedelta(days=offset)
        day_text = day.isoformat()
        try:
            items, source_total = fetcher(day_text, day_text, page=1, rows=rows)
        except Exception as exc:
            return {"status": "ERROR", "error_type": type(exc).__name__,
                    "conclusive": False, "schema_verified": False, "coverage_verified": False,
                    "attempts": attempts + [{"day": day_text, "request_failed": True}]}
        attempts.append({
            "day": day_text,
            "page_rows": len(items),
            "source_total": source_total,
        })
        if items:
            selected_items = items
            selected_total = source_total
            selected_day = day_text
            break
    summary = summarize_rows(selected_items, fields, shape_fn)
    identity_fields = [f for f in fields if f in ('bidNtceNo', 'bidNtceOrd', 'dlvrReqNo', 'prdctSno')]
    verified = bool(selected_items) and all(
        all(field in item for field in fields) and all(_nonempty(item.get(f)) for f in identity_fields)
        for item in selected_items)
    if 'dcsnCntrctNo' in fields:
        verified = verified and all(_nonempty(item.get('dcsnCntrctNo')) or _nonempty(item.get('untyCntrctNo')) for item in selected_items)
    summary.update({
        "selected_day": selected_day,
        "source_total": selected_total,
        "attempts": attempts,
        "conclusive": verified,
        "schema_verified": verified,
        "coverage_verified": False,
    })
    return summary


def run_canary(*, today=None, rows=DEFAULT_ROWS, lookback_days=DEFAULT_LOOKBACK_DAYS):
    db.init_db()
    now = dt.datetime.now(KST)
    probes = {
        "goods_notice": _probe_one_day(
            lambda start, end, page, rows: bid_vnext.fetch_page("goods", start, end, page=page, rows=rows),
            ["bidNtceNo", "bidNtceOrd", "bidNtceNm", "bidNtceDt", "dminsttNm"],
            today=today, rows=rows, lookback_days=lookback_days,
        ),
        "service_notice": _probe_one_day(
            lambda start, end, page, rows: bid_vnext.fetch_page("service", start, end, page=page, rows=rows),
            ["bidNtceNo", "bidNtceOrd", "bidNtceNm", "bidNtceDt", "dminsttNm"],
            today=today, rows=rows, lookback_days=lookback_days,
        ),
        "service_opening": _probe_one_day(
            lambda start, end, page, rows: award_vnext.fetch_page("opening", start, end, page=page, rows=rows),
            ["bidNtceNo", "bidNtceOrd", "opengDt", "prtcptCnum", "opengCorpInfo", "progrsDivCdNm"],
            _opening_shape, today=today, rows=rows, lookback_days=lookback_days,
        ),
        "service_final_award": _probe_one_day(
            lambda start, end, page, rows: award_vnext.fetch_page("award", start, end, page=page, rows=rows),
            ["bidNtceNo", "bidNtceOrd", "bidwinnrNm", "bidwinnrBizno", "sucsfbidAmt", "sucsfbidRate", "rlOpengDt"],
            _award_shape, today=today, rows=rows, lookback_days=lookback_days,
        ),
        "service_contract": _probe_one_day(
            lambda start, end, page, rows: contract_vnext.fetch_page(start, end, page=page, rows=rows),
            ["untyCntrctNo", "dcsnCntrctNo", "ntceNo", "thtmCntrctAmt", "corpList", "cntrctCnclsDate"],
            _contract_shape, today=today, rows=rows, lookback_days=lookback_days,
        ),
        "shopping_delivery": _probe_one_day(
            lambda start, end, page, rows: shopping_vnext.fetch_page(start, end, page=page, rows=rows),
            ["dlvrReqNo", "prdctSno", "cntrctNo", "prdctIdntNo", "prdctClsfcNoNm", "prdctNm"],
            today=today, rows=rows, lookback_days=lookback_days,
        ),
    }
    if set(probes) != set(CANARY_DATASETS):
        raise RuntimeError("canary probe manifest drift detected")
    conclusive = sum(1 for value in probes.values() if value["conclusive"])
    return {
        "status": "CONCLUSIVE" if conclusive == len(probes) else "PARTIAL",
        "coverage_verified": False,
        "live_request_attempted": True,
        "approval_scope": "sample schema only; not lifecycle correctness or whole-source completeness",
        "generated_at_kst": now.isoformat(timespec="seconds"),
        "page_size": int(rows),
        "one_day_windows_max": int(lookback_days),
        "conclusive_probe_count": conclusive,
        "probe_count": len(probes),
        "probes": probes,
    }


def main():
    report = run_canary()
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    with open("g2b_vnext_canary_report.json", "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
