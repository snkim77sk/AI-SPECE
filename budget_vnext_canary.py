"""Sanitized one-page structural canary for 지방재정365 QWGJK budget RAW ingestion."""
from __future__ import annotations

import datetime as dt
import json

from lofin_vnext_http import fetch_budget_page


def _nonempty(value):
    return value not in (None, "", [], {})


def summarize_budget_rows(rows, source_total):
    keys = sorted({str(key) for row in rows for key in row.keys()})
    fields = ["fyr", "laf_cd", "dept_cd", "dbiz_cd", "acnt_dv_cd", "dbiz_nm"]
    stats = {field: {"present": sum(1 for r in rows if field in r),
                     "nonempty": sum(1 for r in rows if _nonempty(r.get(field))),
                     "rows": len(rows)} for field in fields}
    business_name_ok = any(_nonempty(r.get("dbiz_nm")) for r in rows)
    stable_identity_ok = any(_nonempty(r.get("dbiz_cd")) or
                             (_nonempty(r.get("laf_cd")) and _nonempty(r.get("dept_cd")))
                             for r in rows)
    return {"page_rows": len(rows), "source_total": int(source_total or 0), "keys": keys,
            "field_stats": stats,
            "validation": {"business_name": business_name_ok,
                           "stable_identity": stable_identity_ok,
                           "required_ok": bool(rows) and business_name_ok and stable_identity_ok},
            "conclusive": bool(rows) and business_name_ok and stable_identity_ok}


def run_canary(*, fiscal_year=None, snapshot_date=None, rows=100):
    today = dt.date.today()
    year = int(fiscal_year or today.year)
    snapshot = snapshot_date or today.isoformat()
    items, total, code, message = fetch_budget_page(year, snapshot, "", page=1, size=min(max(int(rows),1),1000))
    report = summarize_budget_rows(items, total)
    report.update({"status": "CONCLUSIVE" if report["conclusive"] else "PARTIAL",
                   "fiscal_year": year, "snapshot_date": snapshot,
                   "result_code": str(code or ""), "result_message_present": bool(message),
                   "keyword_filter_used": False, "page": 1})
    return report


def main():
    report = run_canary()
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    with open("budget_vnext_canary_report.json", "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
