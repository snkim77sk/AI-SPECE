"""Sanitized structural canary for independent G2B vNext ingestion paths."""
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
DEFAULT_ROWS = 100
DEFAULT_LOOKBACK_DAYS = 7
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
    return {
        field: {
            "present": sum(1 for row in rows if field in row),
            "nonempty": sum(1 for row in rows if _nonempty(row.get(field))),
            "rows": total,
        }
        for field in fields
    }


def _require_any_nonempty(rows, fields):
    return any(any(_nonempty(row.get(field)) for field in fields) for row in rows)


def _require_any_present(rows, fields):
    return any(any(field in row for field in fields) for row in rows)


def _validate_requirements(rows, requirements):
    checks = {}
    for item in requirements or []:
        name = str(item["name"])
        fields = tuple(item.get("fields") or ())
        mode = str(item.get("mode") or "nonempty")
        if mode == "present":
            ok = _require_any_present(rows, fields)
        else:
            ok = _require_any_nonempty(rows, fields)
        checks[name] = {"ok": bool(ok), "fields": list(fields), "mode": mode}
    return {"required_ok": bool(rows) and all(v["ok"] for v in checks.values()), "checks": checks}


def _opening_shape(rows):
    cases = Counter()
    token_counts = Counter()
    completed = first_rank_candidates = 0
    for row in rows:
        value = str(row.get("opengCorpInfo") or "")
        if value:
            token_counts[len(value.split("^"))] += 1
        parsed = parse_opening_corp_info(value)
        cases[parsed["case"]] += 1
        if str(row.get("progrsDivCdNm") or "").strip() == "개찰완료":
            completed += 1
            first_rank_candidates += int(parsed["case"] == "single")
    return {"opengCorpInfo_token_counts": dict(sorted(token_counts.items())),
            "parser_cases": dict(sorted(cases.items())), "completed_rows": completed,
            "conservative_first_rank_candidates": first_rank_candidates}


def _award_shape(rows):
    return {"rows_with_any_final_award_fact": sum(
        1 for row in rows if any(_nonempty(row.get(k)) for k in
                                 ("bidwinnrNm", "bidwinnrBizno", "sucsfbidAmt")))}


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
        parseable += int(bool(parties))
    return {"ntceNo_length_counts": dict(sorted(ntce_lengths.items())),
            "corpList_entry_token_counts": dict(sorted(corp_token_counts.items())),
            "parsed_party_count_distribution": dict(sorted(party_counts.items())),
            "rows_with_parseable_parties": parseable}


def summarize_rows(rows, fields, shape_fn=None, requirements=None):
    keys = sorted({str(key) for row in rows for key in row.keys()})
    result = {"page_rows": len(rows), "keys": keys,
              "field_stats": _field_stats(rows, fields),
              "validation": _validate_requirements(rows, requirements)}
    if shape_fn:
        result["shape"] = shape_fn(rows)
    return result


def _probe_one_day(fetcher, fields, shape_fn=None, *, requirements=None, today=None,
                   rows=DEFAULT_ROWS, lookback_days=DEFAULT_LOOKBACK_DAYS):
    today = today or dt.datetime.now(KST).date()
    attempts = []
    selected_items, selected_total, selected_day = [], 0, ""
    for offset in range(max(1, int(lookback_days))):
        day = today - dt.timedelta(days=offset)
        day_text = day.isoformat()
        items, source_total = fetcher(day_text, day_text, page=1, rows=rows)
        attempts.append({"day": day_text, "page_rows": len(items),
                         "source_total": int(source_total or 0)})
        if items:
            selected_items = items
            selected_total = int(source_total or 0)
            selected_day = day_text
            break
    summary = summarize_rows(selected_items, fields, shape_fn, requirements=requirements)
    summary.update({"selected_day": selected_day, "source_total": selected_total,
                    "attempts": attempts,
                    "conclusive": bool(selected_items) and bool(summary["validation"]["required_ok"])})
    return summary


def run_canary(*, today=None, rows=DEFAULT_ROWS, lookback_days=DEFAULT_LOOKBACK_DAYS):
    db.init_db()
    common_notice = [{"name": "notice_identity", "fields": ["bidNtceNo", "bidNoticeNo"]}]
    execution_requirements = common_notice + [
        {"name": "execution_identity", "fields": ["bidClsfcNo", "bidClsfNo"], "mode": "present"},
        {"name": "rebid_identity", "fields": ["rbidNo", "rebidNo"], "mode": "present"},
    ]
    probes = {
        "goods_notice": _probe_one_day(
            lambda s,e,page,rows: bid_vnext.fetch_page("goods",s,e,page=page,rows=rows),
            ["bidNtceNo","bidNtceOrd","bidNtceNm","bidNtceDt","dminsttNm"],
            requirements=common_notice, today=today, rows=rows, lookback_days=lookback_days),
        "service_notice": _probe_one_day(
            lambda s,e,page,rows: bid_vnext.fetch_page("service",s,e,page=page,rows=rows),
            ["bidNtceNo","bidNtceOrd","bidNtceNm","bidNtceDt","dminsttNm"],
            requirements=common_notice, today=today, rows=rows, lookback_days=lookback_days),
        "service_opening": _probe_one_day(
            lambda s,e,page,rows: award_vnext.fetch_page("opening",s,e,page=page,rows=rows),
            ["bidNtceNo","bidNtceOrd","bidClsfcNo","rbidNo","opengDt","prtcptCnum","opengCorpInfo","progrsDivCdNm"],
            _opening_shape, requirements=execution_requirements + [
                {"name":"opening_structure","fields":["opengCorpInfo"],"mode":"present"}],
            today=today, rows=rows, lookback_days=lookback_days),
        "service_final_award": _probe_one_day(
            lambda s,e,page,rows: award_vnext.fetch_page("award",s,e,page=page,rows=rows),
            ["bidNtceNo","bidNtceOrd","bidClsfcNo","rbidNo","bidwinnrNm","bidwinnrBizno","sucsfbidAmt","sucsfbidRate","rlOpengDt"],
            _award_shape, requirements=execution_requirements + [
                {"name":"final_award_fact","fields":["bidwinnrNm","bidwinnrBizno","sucsfbidAmt"]}],
            today=today, rows=rows, lookback_days=lookback_days),
        "service_contract": _probe_one_day(
            lambda s,e,page,rows: contract_vnext.fetch_page(s,e,page=page,rows=rows),
            ["untyCntrctNo","dcsnCntrctNo","ntceNo","thtmCntrctAmt","corpList","cntrctCnclsDate"],
            _contract_shape, requirements=[
                {"name":"contract_identity","fields":["untyCntrctNo","dcsnCntrctNo"]},
                {"name":"notice_reference","fields":["ntceNo","bidNtceNo"]},
                {"name":"contract_party_structure","fields":["corpList"],"mode":"present"}],
            today=today, rows=rows, lookback_days=lookback_days),
        "shopping_delivery": _probe_one_day(
            lambda s,e,page,rows: shopping_vnext.fetch_page(s,e,page=page,rows=rows),
            ["dlvrReqNo","prdctSno","cntrctNo","prdctIdntNo","prdctClsfcNoNm","prdctNm"],
            requirements=[{"name":"delivery_identity","fields":["dlvrReqNo","deliveryReqNo","reqNo"]}],
            today=today, rows=rows, lookback_days=lookback_days),
    }
    if set(probes) != set(CANARY_DATASETS):
        raise RuntimeError("canary probe manifest drift detected")
    conclusive = sum(1 for value in probes.values() if value["conclusive"])
    return {"status": "CONCLUSIVE" if conclusive == len(probes) else "PARTIAL",
            "generated_at_kst": dt.datetime.now(KST).isoformat(timespec="seconds"),
            "page_size": int(rows), "one_day_windows_max": int(lookback_days),
            "conclusive_probe_count": conclusive, "probe_count": len(probes), "probes": probes}


def main():
    report = run_canary()
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    with open("g2b_vnext_canary_report.json", "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
