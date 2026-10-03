"""Compact persistence for budget -> shopping match evidence and org patterns.

Only derived match evidence is stored. Source JSON is never copied here.
"""
from __future__ import annotations

from collections import Counter
import datetime as dt
import hashlib
import json

from db import connect

ANALYSIS_VERSION = "budget-shopping-match-v1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_shopping_match_runs(
    run_key TEXT PRIMARY KEY,
    analysis_version TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    categories_json TEXT NOT NULL DEFAULT '[]',
    budget_projects_scanned INTEGER NOT NULL DEFAULT 0,
    shopping_rows_scanned INTEGER NOT NULL DEFAULT 0,
    matched_budget_projects INTEGER NOT NULL DEFAULT 0,
    high_matched_budget_projects INTEGER NOT NULL DEFAULT 0,
    high_matches INTEGER NOT NULL DEFAULT 0,
    candidate_matches INTEGER NOT NULL DEFAULT 0,
    high_matched_shopping_amount INTEGER NOT NULL DEFAULT 0,
    project_match_rate REAL NOT NULL DEFAULT 0,
    evidence_sufficient INTEGER NOT NULL DEFAULT 0,
    expand_2025_recommended INTEGER NOT NULL DEFAULT 0,
    expansion_reasons_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_budget_shopping_match_runs_year_region
    ON budget_shopping_match_runs(fiscal_year,region);

CREATE TABLE IF NOT EXISTS budget_shopping_match_evidence(
    evidence_key TEXT PRIMARY KEY,
    run_key TEXT NOT NULL,
    analysis_version TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    budget_project_identity TEXT NOT NULL DEFAULT '',
    budget_raw_source_key TEXT NOT NULL DEFAULT '',
    budget_region TEXT NOT NULL DEFAULT '',
    budget_org TEXT NOT NULL DEFAULT '',
    budget_dept TEXT NOT NULL DEFAULT '',
    budget_project_code TEXT NOT NULL DEFAULT '',
    budget_project_name TEXT NOT NULL DEFAULT '',
    budget_category TEXT NOT NULL DEFAULT '',
    budget_amount INTEGER NOT NULL DEFAULT 0,
    budget_source_date TEXT NOT NULL DEFAULT '',
    shopping_source_key TEXT NOT NULL DEFAULT '',
    shopping_date TEXT NOT NULL DEFAULT '',
    shopping_org TEXT NOT NULL DEFAULT '',
    shopping_category TEXT NOT NULL DEFAULT '',
    shopping_item TEXT NOT NULL DEFAULT '',
    shopping_delivery_name TEXT NOT NULL DEFAULT '',
    shopping_vendor TEXT NOT NULL DEFAULT '',
    shopping_amount INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL DEFAULT 0,
    level TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    shared_signals_json TEXT NOT NULL DEFAULT '[]',
    shared_tokens_json TEXT NOT NULL DEFAULT '[]',
    lag_days INTEGER,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_budget_shopping_match_evidence_org_year
    ON budget_shopping_match_evidence(budget_org,fiscal_year);
CREATE INDEX IF NOT EXISTS ix_budget_shopping_match_evidence_level_score
    ON budget_shopping_match_evidence(level,score);
CREATE INDEX IF NOT EXISTS ix_budget_shopping_match_evidence_shopping
    ON budget_shopping_match_evidence(shopping_source_key);
"""


def ensure_schema():
    with connect() as conn:
        conn.executescript(SCHEMA)


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hash(parts):
    return hashlib.sha256(
        "|".join(str(value or "") for value in parts).encode("utf-8")
    ).hexdigest()


def _run_key(summary):
    return _hash((
        ANALYSIS_VERSION,
        int(summary.get("fiscal_year") or 0),
        str(summary.get("region") or ""),
        _canonical_json(summary.get("categories") or []),
    ))


def _evidence_key(run_key, row):
    return _hash((
        run_key,
        str(row.get("budget_project_identity") or ""),
        str(row.get("budget_raw_source_key") or ""),
        str(row.get("shopping_source_key") or ""),
    ))


def save_match_summary(summary):
    """Replace one deterministic analysis run with its latest derived evidence."""
    ensure_schema()
    payload = dict(summary or {})
    run_key = _run_key(payload)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    matches = list(payload.get("matches") or [])

    run_values = (
        run_key,
        ANALYSIS_VERSION,
        int(payload.get("fiscal_year") or 0),
        str(payload.get("region") or ""),
        _canonical_json(payload.get("categories") or []),
        int(payload.get("budget_projects_scanned") or 0),
        int(payload.get("shopping_rows_scanned") or 0),
        int(payload.get("matched_budget_projects") or 0),
        int(payload.get("high_matched_budget_projects") or 0),
        int(payload.get("high_matches") or 0),
        int(payload.get("candidate_matches") or 0),
        int(payload.get("high_matched_shopping_amount") or 0),
        float(payload.get("project_match_rate") or 0),
        1 if bool(payload.get("evidence_sufficient_for_pattern_learning")) else 0,
        1 if bool(payload.get("expand_2025_recommended")) else 0,
        _canonical_json(payload.get("expansion_reasons") or []),
        now,
    )

    evidence_rows = []
    for row in matches:
        lag = row.get("lag_days")
        evidence_rows.append((
            _evidence_key(run_key, row),
            run_key,
            ANALYSIS_VERSION,
            int(row.get("fiscal_year") or payload.get("fiscal_year") or 0),
            str(row.get("budget_project_identity") or ""),
            str(row.get("budget_raw_source_key") or ""),
            str(row.get("budget_region") or ""),
            str(row.get("budget_org") or ""),
            str(row.get("budget_dept") or ""),
            str(row.get("budget_project_code") or ""),
            str(row.get("budget_project_name") or ""),
            str(row.get("budget_category") or ""),
            int(row.get("budget_amount") or 0),
            str(row.get("budget_source_date") or ""),
            str(row.get("shopping_source_key") or ""),
            str(row.get("shopping_date") or ""),
            str(row.get("shopping_org") or ""),
            str(row.get("shopping_category") or ""),
            str(row.get("shopping_item") or ""),
            str(row.get("shopping_delivery_name") or ""),
            str(row.get("shopping_vendor") or ""),
            int(row.get("shopping_amount") or 0),
            int(row.get("score") or 0),
            str(row.get("level") or ""),
            _canonical_json(row.get("evidence") or []),
            _canonical_json(row.get("shared_signals") or []),
            _canonical_json(row.get("shared_tokens") or []),
            int(lag) if lag is not None else None,
            now,
        ))

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM budget_shopping_match_evidence WHERE run_key=?",
            (run_key,),
        )
        conn.execute(
            """INSERT INTO budget_shopping_match_runs(
                   run_key,analysis_version,fiscal_year,region,categories_json,
                   budget_projects_scanned,shopping_rows_scanned,
                   matched_budget_projects,high_matched_budget_projects,
                   high_matches,candidate_matches,high_matched_shopping_amount,
                   project_match_rate,evidence_sufficient,expand_2025_recommended,
                   expansion_reasons_json,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_key) DO UPDATE SET
                   analysis_version=excluded.analysis_version,
                   fiscal_year=excluded.fiscal_year,
                   region=excluded.region,
                   categories_json=excluded.categories_json,
                   budget_projects_scanned=excluded.budget_projects_scanned,
                   shopping_rows_scanned=excluded.shopping_rows_scanned,
                   matched_budget_projects=excluded.matched_budget_projects,
                   high_matched_budget_projects=excluded.high_matched_budget_projects,
                   high_matches=excluded.high_matches,
                   candidate_matches=excluded.candidate_matches,
                   high_matched_shopping_amount=excluded.high_matched_shopping_amount,
                   project_match_rate=excluded.project_match_rate,
                   evidence_sufficient=excluded.evidence_sufficient,
                   expand_2025_recommended=excluded.expand_2025_recommended,
                   expansion_reasons_json=excluded.expansion_reasons_json,
                   updated_at=excluded.updated_at""",
            run_values,
        )
        if evidence_rows:
            conn.executemany(
                """INSERT INTO budget_shopping_match_evidence(
                       evidence_key,run_key,analysis_version,fiscal_year,
                       budget_project_identity,budget_raw_source_key,
                       budget_region,budget_org,budget_dept,budget_project_code,
                       budget_project_name,budget_category,budget_amount,
                       budget_source_date,shopping_source_key,shopping_date,
                       shopping_org,shopping_category,shopping_item,
                       shopping_delivery_name,shopping_vendor,shopping_amount,
                       score,level,evidence_json,shared_signals_json,
                       shared_tokens_json,lag_days,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                evidence_rows,
            )

    return {
        "run_key": run_key,
        "analysis_version": ANALYSIS_VERSION,
        "saved_matches": len(evidence_rows),
        "fiscal_year": int(payload.get("fiscal_year") or 0),
        "region": str(payload.get("region") or ""),
        "updated_at": now,
    }


def match_run_rows(*, fiscal_year=None, region=None, limit=50):
    ensure_schema()
    where = ["analysis_version=?"]
    params = [ANALYSIS_VERSION]
    if fiscal_year is not None:
        where.append("fiscal_year=?")
        params.append(int(fiscal_year))
    if region is not None:
        where.append("region=?")
        params.append(str(region or ""))
    params.append(max(1, min(int(limit), 500)))
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT * FROM budget_shopping_match_runs
                WHERE {' AND '.join(where)}
                ORDER BY fiscal_year DESC,updated_at DESC
                LIMIT ?""",
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def organization_patterns(*, fiscal_years=None, region="", min_score=80, limit=200):
    """Aggregate persisted evidence into descriptive institution purchase patterns."""
    ensure_schema()
    where = ["analysis_version=?"]
    params = [ANALYSIS_VERSION]
    years = sorted({int(value) for value in (fiscal_years or []) if int(value) > 0})
    if years:
        where.append("fiscal_year IN (" + ",".join("?" for _ in years) + ")")
        params.extend(years)
    if str(region or "").strip():
        where.append("budget_region=?")
        params.append(str(region).strip())

    with connect() as conn:
        rows = conn.execute(
            f"""SELECT * FROM budget_shopping_match_evidence
                WHERE {' AND '.join(where)}
                ORDER BY budget_org,fiscal_year,score DESC,shopping_date""",
            tuple(params),
        ).fetchall()

    groups = {}
    for raw in rows:
        row = dict(raw)
        org = str(row.get("budget_org") or "").strip()
        if not org:
            continue
        item = groups.setdefault(org, {
            "org_name": org,
            "evidence_years": set(),
            "high_rows": 0,
            "candidate_rows": 0,
            "projects": {},
            "shopping": {},
            "lags": [],
            "signals": Counter(),
            "budget_categories": set(),
            "shopping_categories": set(),
        })
        item["evidence_years"].add(int(row.get("fiscal_year") or 0))
        if str(row.get("level") or "") == "CANDIDATE":
            item["candidate_rows"] += 1
        if int(row.get("score") or 0) < int(min_score):
            continue

        item["high_rows"] += 1
        project_key = (
            str(row.get("budget_project_identity") or "")
            or str(row.get("budget_raw_source_key") or "")
        )
        shopping_key = str(row.get("shopping_source_key") or "")
        if project_key:
            item["projects"][project_key] = max(
                int(item["projects"].get(project_key) or 0),
                int(row.get("budget_amount") or 0),
            )
        if shopping_key:
            item["shopping"][shopping_key] = max(
                int(item["shopping"].get(shopping_key) or 0),
                int(row.get("shopping_amount") or 0),
            )

        lag = row.get("lag_days")
        if lag is not None and int(lag) >= 0:
            item["lags"].append(int(lag))
        try:
            signals = json.loads(str(row.get("shared_signals_json") or "[]"))
        except (TypeError, ValueError):
            signals = []
        for signal in signals if isinstance(signals, list) else []:
            if str(signal):
                item["signals"][str(signal)] += 1
        if str(row.get("budget_category") or ""):
            item["budget_categories"].add(str(row["budget_category"]))
        if str(row.get("shopping_category") or ""):
            item["shopping_categories"].add(str(row["shopping_category"]))

    result = []
    for item in groups.values():
        matched_budget = sum(item["projects"].values())
        shopping_amount = sum(item["shopping"].values())
        lags = item["lags"]
        result.append({
            "org_name": item["org_name"],
            "evidence_years": sorted(year for year in item["evidence_years"] if year),
            "high_match_rows": int(item["high_rows"]),
            "candidate_match_rows": int(item["candidate_rows"]),
            "high_matched_budget_projects": len(item["projects"]),
            "high_matched_shopping_rows": len(item["shopping"]),
            "matched_budget_amount": matched_budget,
            "actual_shopping_amount": shopping_amount,
            "shopping_to_budget_amount_ratio": (
                round(shopping_amount / matched_budget, 4)
                if matched_budget > 0 else 0.0
            ),
            "average_nonnegative_lag_days": (
                round(sum(lags) / len(lags), 1) if lags else None
            ),
            "signal_counts": dict(item["signals"].most_common()),
            "budget_categories": sorted(item["budget_categories"]),
            "shopping_categories": sorted(item["shopping_categories"]),
            "pattern_basis": "PERSISTED_HIGH_MATCH_EVIDENCE",
        })

    result.sort(
        key=lambda row: (
            int(row["high_matched_budget_projects"]),
            int(row["actual_shopping_amount"]),
            int(row["high_match_rows"]),
            str(row["org_name"]),
        ),
        reverse=True,
    )
    return result[:max(1, min(int(limit), 1000))]
