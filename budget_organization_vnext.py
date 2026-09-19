"""Organization/query layer for the additive G2B vNext budget projection.

This module does not collect source data and does not replace legacy AI-SPECE tables.
It organizes already-preserved budget RAW/projection rows into:

* a stable project identity across repeated QWGJK snapshots,
* latest/current state plus full timeline,
* projection coverage for CURRENT_STORED_RAW_ONLY,
* conservative exact AIDFA -> QWGJK structural context links.

No fuzzy name matching is performed here; ambiguous links stay unlinked.
"""
from __future__ import annotations

from db import connect
import budget_projection_vnext

BUDGET_DATASETS = ("budget", "budget_appropriation", "education_budget")


def _identity_sql(alias="p"):
    p = str(alias)
    org = (
        f"COALESCE(NULLIF(TRIM({p}.org_code),''),NULLIF(TRIM({p}.org_name),''),"
        f"NULLIF(TRIM({p}.region_code),''),NULLIF(TRIM({p}.region_name),''),'UNKNOWN_ORG')"
    )
    project = (
        f"COALESCE(NULLIF(TRIM({p}.project_code),''),NULLIF(TRIM({p}.project_name),''),"
        f"{p}.raw_source_key)"
    )
    account = (
        f"COALESCE(NULLIF(TRIM({p}.account_code),''),"
        f"NULLIF(TRIM({p}.account_name),''),'')"
    )
    return f"""CASE
        WHEN {p}.source_layer='DETAIL_EXECUTION' THEN
            'DETAIL_EXECUTION|' || {p}.fiscal_year || '|' || {org} || '|' || {project} || '|' || {account}
        WHEN {p}.source_layer='EDUCATION' THEN
            'EDUCATION|' || {p}.fiscal_year || '|' || {org} || '|' || {project} || '|' ||
            COALESCE(NULLIF(TRIM({p}.source_operation),''),NULLIF(TRIM({p}.source_system),''),'UNKNOWN_EDUCATION_SOURCE') || '|' || {account}
        WHEN {p}.source_layer='APPROPRIATION' THEN
            'APPROPRIATION|' || {p}.fiscal_year || '|' || {org} || '|' ||
            COALESCE(NULLIF(TRIM({p}.field_name),''),'UNKNOWN_FIELD') || '|' ||
            COALESCE(NULLIF(TRIM({p}.section_name),''),'UNKNOWN_SECTION') || '|' || {account}
        ELSE {p}.source_layer || '|' || {p}.fiscal_year || '|' || {p}.raw_dataset || '|' || {p}.raw_source_key
    END"""


def _current_cte(where_sql="", *, alias="p"):
    identity = _identity_sql(alias)
    return f"""
        WITH base AS (
            SELECT {alias}.*, {identity} AS project_identity
            FROM vnext_budget_projection {alias}
            {where_sql}
        ), ranked AS (
            SELECT base.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY project_identity
                       ORDER BY CASE WHEN COALESCE(snapshot_date,'')='' THEN '0000-00-00' ELSE snapshot_date END DESC,
                                updated_at DESC, raw_source_key DESC
                   ) AS _rn
            FROM base
        )
    """


def current_budget_state(*, fiscal_year=None, source_layers=None):
    """Return one latest row per stable budget/project identity.

    QWGJK repeated snapshots collapse to the newest snapshot while all source rows
    remain in ``vnext_budget_projection`` and are available through ``budget_timeline``.
    """
    budget_projection_vnext.ensure_schema()
    filters = []
    params = []
    if fiscal_year is not None:
        filters.append("p.fiscal_year=?")
        params.append(int(fiscal_year))
    layers = tuple(str(x) for x in (source_layers or ()) if str(x))
    if layers:
        filters.append("p.source_layer IN (" + ",".join("?" for _ in layers) + ")")
        params.extend(layers)
    where = "WHERE " + " AND ".join(filters) if filters else ""
    sql = _current_cte(where) + " SELECT * FROM ranked WHERE _rn=1 ORDER BY fiscal_year DESC,source_layer,org_name,project_name"
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]


def budget_timeline(project_identity):
    """Return every preserved projection row for one stable project identity."""
    budget_projection_vnext.ensure_schema()
    identity = str(project_identity or "").strip()
    if not identity:
        return []
    expr = _identity_sql("p")
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT p.*, {expr} AS project_identity
                FROM vnext_budget_projection p
                WHERE {expr}=?
                ORDER BY CASE WHEN COALESCE(p.snapshot_date,'')='' THEN '0000-00-00' ELSE p.snapshot_date END,
                         p.updated_at, p.raw_source_key""",
            (identity,),
        ).fetchall()
    return [dict(row) for row in rows]


def projection_coverage():
    """Measure organization coverage for current stored RAW only.

    This intentionally does NOT claim whole-source collection completeness. A row is
    organized only when the projection exists for the same current RAW payload hash.
    """
    budget_projection_vnext.ensure_schema()
    result = []
    with connect() as conn:
        for dataset in BUDGET_DATASETS:
            raw_rows = conn.execute(
                "SELECT COUNT(*) AS n FROM raw_records WHERE dataset=?", (dataset,)
            ).fetchone()["n"]
            current_rows = conn.execute(
                """SELECT COUNT(*) AS n
                   FROM raw_records r
                   JOIN vnext_budget_projection p
                     ON p.raw_dataset=r.dataset AND p.raw_source_key=r.source_key
                    AND p.payload_sha256=r.payload_sha256
                   WHERE r.dataset=?""",
                (dataset,),
            ).fetchone()["n"]
            missing = int(raw_rows) - int(current_rows)
            result.append({
                "dataset": dataset,
                "raw_rows": int(raw_rows),
                "projection_rows_current": int(current_rows),
                "stale_or_missing_rows": int(missing),
                "projection_complete_for_current_raw": int(missing) == 0,
                "coverage_scope": "CURRENT_STORED_RAW_ONLY",
                "source_collection_completeness_verified": False,
            })
    return result


def exact_appropriation_detail_links(*, fiscal_year=None):
    """Return conservative structural context matches from AIDFA to QWGJK.

    A relation is emitted only when fiscal year, local-government identity, field and
    section names are all non-empty and exactly equal. This is a hierarchy/context
    relation, not a claim that the appropriation row is a one-to-one project budget.
    """
    budget_projection_vnext.ensure_schema()
    filters = ["a.source_layer='APPROPRIATION'", "d.source_layer='DETAIL_EXECUTION'",
               "a.fiscal_year=d.fiscal_year",
               "TRIM(a.org_code)<>''", "a.org_code=d.org_code",
               "TRIM(a.field_name)<>''", "a.field_name=d.field_name",
               "TRIM(a.section_name)<>''", "a.section_name=d.section_name"]
    params = []
    if fiscal_year is not None:
        filters.append("a.fiscal_year=?")
        params.append(int(fiscal_year))
    where = " AND ".join(filters)
    a_identity = _identity_sql("a")
    d_identity = _identity_sql("d")
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT
                       {a_identity} AS appropriation_identity,
                       {d_identity} AS detail_identity,
                       a.fiscal_year,a.org_code,a.org_name,a.field_name,a.section_name,
                       a.raw_source_key AS appropriation_raw_key,
                       d.raw_source_key AS detail_raw_key,
                       'EXACT_ORG_FIELD_SECTION' AS match_basis,
                       1.0 AS confidence
                FROM vnext_budget_projection a
                JOIN vnext_budget_projection d ON {where}
                ORDER BY a.fiscal_year,a.org_name,a.field_name,a.section_name,d.project_name""",
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def organization_summary(*, fiscal_year=None):
    current = current_budget_state(fiscal_year=fiscal_year)
    by_layer = {}
    for row in current:
        layer = str(row.get("source_layer") or "UNKNOWN")
        item = by_layer.setdefault(layer, {
            "projects": 0, "budget_amount": 0, "executed_amount": 0, "remaining_amount": 0,
        })
        item["projects"] += 1
        item["budget_amount"] += int(row.get("budget_amount") or 0)
        item["executed_amount"] += int(row.get("executed_amount") or 0)
        item["remaining_amount"] += int(row.get("remaining_amount") or 0)
    return {
        "fiscal_year": int(fiscal_year) if fiscal_year is not None else None,
        "current_projects": len(current),
        "by_layer": by_layer,
        "projection_coverage": projection_coverage(),
        "source_collection_completeness_verified": False,
    }
