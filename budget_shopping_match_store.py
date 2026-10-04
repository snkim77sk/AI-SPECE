"""Compact persistence for budget -> shopping match evidence and org patterns.

Only derived match evidence is stored. Source JSON is never copied here.
"""
from __future__ import annotations

from collections import Counter
import datetime as dt
import hashlib
import json

import admin_geography_v41
from db import connect

ANALYSIS_VERSION = "budget-shopping-match-v8-admin-geography-history"

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

CREATE TABLE IF NOT EXISTS budget_shopping_match_projects(
    project_evidence_key TEXT PRIMARY KEY,
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
    best_match_score INTEGER NOT NULL DEFAULT 0,
    best_match_level TEXT NOT NULL DEFAULT 'UNMATCHED',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_budget_shopping_match_projects_org_year
    ON budget_shopping_match_projects(budget_org,fiscal_year);
CREATE INDEX IF NOT EXISTS ix_budget_shopping_match_projects_level_score
    ON budget_shopping_match_projects(best_match_level,best_match_score);

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


def _project_key(row):
    return (
        str(row.get("budget_project_identity") or "").strip()
        or str(row.get("budget_raw_source_key") or "").strip()
    )


def _project_evidence_key(run_key, row):
    return _hash((run_key, _project_key(row), "BUDGET_PROJECT"))


def _shopping_match_key(row):
    request_no = str(row.get("shopping_delivery_req_no") or "").strip()
    if request_no:
        return ("REQUEST", request_no)
    source_key = str(row.get("shopping_source_key") or "").strip()
    if source_key:
        return ("SOURCE", source_key)
    return (
        "FALLBACK",
        str(row.get("shopping_date") or ""),
        str(row.get("shopping_org") or ""),
        str(row.get("shopping_category") or ""),
        str(row.get("shopping_item") or ""),
        str(row.get("shopping_delivery_name") or ""),
        str(row.get("shopping_vendor") or ""),
        int(row.get("shopping_amount") or 0),
    )


def _match_assignment_rank(row):
    budget_amount = int(row.get("budget_amount") or 0)
    shopping_amount = int(row.get("shopping_amount") or 0)
    amount_gap = (
        abs(budget_amount - shopping_amount)
        if budget_amount > 0 and shopping_amount > 0
        else 10**30
    )
    lag = row.get("lag_days")
    lag_value = int(lag) if lag is not None else None
    return (
        -int(row.get("score") or 0),
        -(
            1
            if str(row.get("organization_basis") or "") == "EXACT_ORG_NAME"
            else 0
        ),
        -len(row.get("shared_signals") or []),
        -len(row.get("shared_tokens") or []),
        amount_gap,
        0 if lag_value is not None and lag_value >= 0 else 1,
        abs(lag_value) if lag_value is not None else 10**9,
        _project_key(row),
    )


def _unique_shopping_matches(rows):
    winners = {}
    for row in rows:
        key = _shopping_match_key(row)
        current = winners.get(key)
        if current is None or _match_assignment_rank(row) < _match_assignment_rank(current):
            winners[key] = row
    return list(winners.values())


def save_match_summary(summary):
    """Replace one deterministic analysis run with its latest derived evidence."""
    ensure_schema()
    payload = dict(summary or {})
    run_key = _run_key(payload)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    matches = _unique_shopping_matches([
        row
        for row in list(payload.get("matches") or [])
        if bool(row.get("shopping_request_integrity_valid", True))
    ])
    population_complete = bool(
        payload.get("match_population_complete")
    )
    budget_projects = (
        list(payload.get("budget_projects") or [])
        if population_complete
        else []
    )

    best_match_by_project = {}
    for row in matches:
        key = _project_key(row)
        if not key:
            continue
        rank = (
            int(row.get("score") or 0),
            1 if str(row.get("level") or "") == "HIGH" else 0,
        )
        current = best_match_by_project.get(key)
        if current is None or rank > current[0]:
            best_match_by_project[key] = (rank, row)

    high_matches = [
        row for row in matches
        if str(row.get("level") or "") == "HIGH"
    ]
    candidate_matches = [
        row for row in matches
        if str(row.get("level") or "") == "CANDIDATE"
    ]
    matched_project_keys = {
        _project_key(row)
        for row in matches
        if _project_key(row)
    }
    high_project_keys = {
        _project_key(row)
        for row in high_matches
        if _project_key(row)
    }
    budget_count = int(payload.get("budget_projects_scanned") or 0)
    project_match_rate = (
        len(matched_project_keys) / budget_count
        if budget_count > 0 else 0.0
    )

    run_values = (
        run_key,
        ANALYSIS_VERSION,
        int(payload.get("fiscal_year") or 0),
        str(payload.get("region") or ""),
        _canonical_json(payload.get("categories") or []),
        budget_count,
        int(payload.get("shopping_rows_scanned") or 0),
        len(matched_project_keys),
        len(high_project_keys),
        len(high_matches),
        len(candidate_matches),
        sum(int(row.get("shopping_amount") or 0) for row in high_matches),
        float(project_match_rate),
        1 if bool(payload.get("evidence_sufficient_for_pattern_learning")) else 0,
        1 if bool(payload.get("expand_2025_recommended")) else 0,
        _canonical_json(payload.get("expansion_reasons") or []),
        now,
    )

    project_rows = []
    for row in budget_projects:
        key = _project_key(row)
        if not key:
            continue
        best = best_match_by_project.get(key)
        best_row = best[1] if best else {}
        project_rows.append((
            _project_evidence_key(run_key, row),
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
            int(best_row.get("score") or 0),
            str(best_row.get("level") or "UNMATCHED"),
            now,
        ))

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
        if not population_complete:
            existing_projects = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM budget_shopping_match_projects
                   WHERE run_key=? AND analysis_version=?""",
                (run_key, ANALYSIS_VERSION),
            ).fetchone()
            existing_project_count = int(
                (existing_projects["count"] if existing_projects else 0) or 0
            )
            if existing_project_count > 0:
                existing_evidence = conn.execute(
                    """SELECT COUNT(*) AS count
                       FROM budget_shopping_match_evidence
                       WHERE run_key=? AND analysis_version=?""",
                    (run_key, ANALYSIS_VERSION),
                ).fetchone()
                existing_run = conn.execute(
                    """SELECT fiscal_year,region,updated_at
                       FROM budget_shopping_match_runs
                       WHERE run_key=? AND analysis_version=?""",
                    (run_key, ANALYSIS_VERSION),
                ).fetchone()
                return {
                    "run_key": run_key,
                    "analysis_version": ANALYSIS_VERSION,
                    "saved_matches": int(
                        (existing_evidence["count"] if existing_evidence else 0) or 0
                    ),
                    "saved_budget_projects": existing_project_count,
                    "match_population_complete": True,
                    "incoming_match_population_complete": False,
                    "preserved_verified_population": True,
                    "fiscal_year": int(
                        (
                            existing_run["fiscal_year"]
                            if existing_run
                            else payload.get("fiscal_year")
                        )
                        or 0
                    ),
                    "region": str(
                        (
                            existing_run["region"]
                            if existing_run
                            else payload.get("region")
                        )
                        or ""
                    ),
                    "updated_at": str(
                        (existing_run["updated_at"] if existing_run else "") or now
                    ),
                }

        conn.execute(
            "DELETE FROM budget_shopping_match_evidence WHERE run_key=?",
            (run_key,),
        )
        conn.execute(
            "DELETE FROM budget_shopping_match_projects WHERE run_key=?",
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
        if project_rows:
            conn.executemany(
                """INSERT INTO budget_shopping_match_projects(
                       project_evidence_key,run_key,analysis_version,fiscal_year,
                       budget_project_identity,budget_raw_source_key,
                       budget_region,budget_org,budget_dept,budget_project_code,
                       budget_project_name,budget_category,budget_amount,
                       budget_source_date,best_match_score,best_match_level,
                       updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                project_rows,
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
        "saved_budget_projects": len(project_rows),
        "match_population_complete": population_complete,
        "incoming_match_population_complete": population_complete,
        "preserved_verified_population": False,
        "fiscal_year": int(payload.get("fiscal_year") or 0),
        "region": str(payload.get("region") or ""),
        "updated_at": now,
    }


def match_run_rows(*, fiscal_year=None, region=None, limit=50):
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
    """Aggregate budget population + persisted evidence into institution patterns."""
    years = sorted({int(value) for value in (fiscal_years or []) if int(value) > 0})

    project_where = ["analysis_version=?"]
    project_params = [ANALYSIS_VERSION]
    evidence_where = ["analysis_version=?"]
    evidence_params = [ANALYSIS_VERSION]
    if years:
        placeholders = ",".join("?" for _ in years)
        project_where.append(f"fiscal_year IN ({placeholders})")
        project_params.extend(years)
        evidence_where.append(f"fiscal_year IN ({placeholders})")
        evidence_params.extend(years)
    if str(region or "").strip():
        region_members = admin_geography_v41.region_history_members(region)
        if not region_members:
            return []
        placeholders = ",".join("?" for _ in region_members)
        project_where.append(f"budget_region IN ({placeholders})")
        project_params.extend(region_members)
        evidence_where.append(f"budget_region IN ({placeholders})")
        evidence_params.extend(region_members)

    with connect() as conn:
        project_rows = conn.execute(
            f"""SELECT * FROM budget_shopping_match_projects
                WHERE {' AND '.join(project_where)}
                ORDER BY budget_org,fiscal_year,budget_project_name""",
            tuple(project_params),
        ).fetchall()
        evidence_rows = conn.execute(
            f"""SELECT * FROM budget_shopping_match_evidence
                WHERE {' AND '.join(evidence_where)}
                ORDER BY budget_org,fiscal_year,score DESC,shopping_date""",
            tuple(evidence_params),
        ).fetchall()

    groups = {}

    def group_for(lineage):
        group_key = str(lineage.get("group_key") or "").strip()
        org_name = str(lineage.get("org_name") or "").strip()
        if not group_key or not org_name:
            return None
        item = groups.setdefault(group_key, {
            "org_name": org_name,
            "historical_org_names": set(),
            "lineage_basis": Counter(),
            "evidence_years": set(),
            "population": {},
            "population_complete": False,
            "high_rows": 0,
            "candidate_rows": 0,
            "legacy_high_projects": {},
            "legacy_matched_projects": set(),
            "shopping": {},
            "lags": [],
            "signals": Counter(),
            "budget_categories": set(),
            "shopping_categories": set(),
        })
        source_org = str(lineage.get("source_org") or "").strip()
        if source_org:
            item["historical_org_names"].add(source_org)
        basis = str(lineage.get("basis") or "").strip()
        if basis:
            item["lineage_basis"][basis] += 1
        return item

    for raw in project_rows:
        row = dict(raw)
        org = str(row.get("budget_org") or "").strip()
        if not org:
            continue
        lineage = admin_geography_v41.organization_lineage(
            org=org,
            project_text=str(row.get("budget_project_name") or ""),
            source_date=str(row.get("budget_source_date") or ""),
            region=str(row.get("budget_region") or ""),
        )
        item = group_for(lineage)
        if item is None:
            continue
        item["population_complete"] = True
        year = int(row.get("fiscal_year") or 0)
        if year:
            item["evidence_years"].add(year)
        project_key = (
            str(row.get("budget_project_identity") or "").strip()
            or str(row.get("budget_raw_source_key") or "").strip()
        )
        if not project_key:
            continue
        key = (year, project_key)
        candidate = {
            "budget_amount": int(row.get("budget_amount") or 0),
            "best_match_score": int(row.get("best_match_score") or 0),
            "best_match_level": str(row.get("best_match_level") or "UNMATCHED"),
            "budget_category": str(row.get("budget_category") or ""),
        }
        current = item["population"].get(key)
        if (
            current is None
            or candidate["best_match_score"] > current["best_match_score"]
        ):
            item["population"][key] = candidate
        if candidate["budget_category"]:
            item["budget_categories"].add(candidate["budget_category"])

    for raw in evidence_rows:
        row = dict(raw)
        org = str(row.get("budget_org") or "").strip()
        if not org:
            continue
        lineage = admin_geography_v41.organization_lineage(
            org=org,
            project_text=str(row.get("budget_project_name") or ""),
            source_date=str(row.get("budget_source_date") or ""),
            region=str(row.get("budget_region") or ""),
        )
        item = group_for(lineage)
        if item is None:
            continue
        year = int(row.get("fiscal_year") or 0)
        if year:
            item["evidence_years"].add(year)

        project_key = (
            str(row.get("budget_project_identity") or "").strip()
            or str(row.get("budget_raw_source_key") or "").strip()
        )
        if project_key:
            item["legacy_matched_projects"].add((year, project_key))

        if str(row.get("level") or "") == "CANDIDATE":
            item["candidate_rows"] += 1
        if int(row.get("score") or 0) < int(min_score):
            continue

        item["high_rows"] += 1
        if project_key:
            item["legacy_high_projects"][(year, project_key)] = max(
                int(item["legacy_high_projects"].get((year, project_key)) or 0),
                int(row.get("budget_amount") or 0),
            )

        shopping_key = str(row.get("shopping_source_key") or "")
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
        population = item["population"]
        population_complete = bool(item["population_complete"] and population)
        if population_complete:
            total_projects = len(population)
            total_budget_amount = sum(
                int(project["budget_amount"] or 0)
                for project in population.values()
            )
            high_projects = {
                key: project
                for key, project in population.items()
                if int(project["best_match_score"] or 0) >= int(min_score)
            }
            matched_projects = {
                key: project
                for key, project in population.items()
                if str(project["best_match_level"] or "") in {"HIGH", "CANDIDATE"}
            }
            candidate_projects = {
                key: project
                for key, project in matched_projects.items()
                if key not in high_projects
            }
            matched_budget = sum(
                int(project["budget_amount"] or 0)
                for project in high_projects.values()
            )
            high_project_count = len(high_projects)
            matched_project_count = len(matched_projects)
            candidate_project_count = len(candidate_projects)
            high_match_rate = (
                round(high_project_count / total_projects, 4)
                if total_projects else None
            )
            matched_rate = (
                round(matched_project_count / total_projects, 4)
                if total_projects else None
            )
        else:
            total_projects = 0
            total_budget_amount = 0
            high_project_count = len(item["legacy_high_projects"])
            matched_project_count = len(item["legacy_matched_projects"])
            candidate_project_count = max(
                0, matched_project_count - high_project_count
            )
            matched_budget = sum(item["legacy_high_projects"].values())
            high_match_rate = None
            matched_rate = None

        shopping_amount = sum(item["shopping"].values())
        lags = item["lags"]
        result.append({
            "org_name": item["org_name"],
            "historical_org_names": sorted(item["historical_org_names"]),
            "organization_lineage_applied": (
                len(item["historical_org_names"]) > 1
                or any(
                    basis not in {"SOURCE_ORG", "CURRENT_ORG"}
                    for basis in item["lineage_basis"]
                )
            ),
            "organization_lineage_basis": dict(
                item["lineage_basis"].most_common()
            ),
            "evidence_years": sorted(
                year for year in item["evidence_years"] if year
            ),
            "population_complete": population_complete,
            "historical_budget_projects": total_projects,
            "historical_budget_amount": total_budget_amount,
            "high_match_rows": int(item["high_rows"]),
            "candidate_match_rows": int(item["candidate_rows"]),
            "high_matched_budget_projects": high_project_count,
            "matched_budget_projects": matched_project_count,
            "candidate_matched_budget_projects": candidate_project_count,
            "high_match_project_rate": high_match_rate,
            "matched_project_rate": matched_rate,
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
            "pattern_basis": (
                "PERSISTED_BUDGET_POPULATION_AND_HIGH_MATCH_EVIDENCE"
                if population_complete
                else "PERSISTED_HIGH_MATCH_EVIDENCE_LEGACY"
            ),
        })

    result.sort(
        key=lambda row: (
            int(row["high_matched_budget_projects"]),
            int(row["historical_budget_projects"]),
            int(row["actual_shopping_amount"]),
            str(row["org_name"]),
        ),
        reverse=True,
    )
    return result[:max(1, min(int(limit), 1000))]

