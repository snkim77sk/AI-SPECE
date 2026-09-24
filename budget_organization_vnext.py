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

import json

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
    department = (
        f"COALESCE(NULLIF(TRIM({p}.dept_code),''),"
        f"NULLIF(TRIM({p}.dept_name),''),'')"
    )
    institution = (
        f"COALESCE(NULLIF(TRIM({p}.institution_code),''),"
        f"NULLIF(TRIM({p}.institution_name),''),'')"
    )
    account = (
        f"COALESCE(NULLIF(TRIM({p}.account_code),''),"
        f"NULLIF(TRIM({p}.account_name),''),'')"
    )
    field = (
        f"COALESCE(NULLIF(TRIM({p}.field_code),''),"
        f"NULLIF(TRIM({p}.field_name),''),'UNKNOWN_FIELD')"
    )
    section = (
        f"COALESCE(NULLIF(TRIM({p}.section_code),''),"
        f"NULLIF(TRIM({p}.section_name),''),'UNKNOWN_SECTION')"
    )
    education_partition = (
        f"CASE WHEN instr(COALESCE({p}.source_operation,''),':')>0 "
        f"THEN substr({p}.source_operation,instr({p}.source_operation,':')+1) "
        f"ELSE COALESCE(NULLIF(TRIM({p}.source_operation),''),"
        f"NULLIF(TRIM({p}.source_system),''),'UNKNOWN_EDUCATION_SOURCE') END"
    )
    return f"""CASE
        WHEN {p}.source_layer='DETAIL_EXECUTION' THEN
            'DETAIL_EXECUTION|' || {p}.fiscal_year || '|' || {org} || '|' ||
            {department} || '|' || {project} || '|' || {account}
        WHEN {p}.source_layer='EDUCATION' THEN
            'EDUCATION|' || {p}.fiscal_year || '|' || {org} || '|' ||
            {institution} || '|' || {department} || '|' || {project} || '|' ||
            {education_partition} || '|' || {account}
        WHEN {p}.source_layer='APPROPRIATION' THEN
            CASE
                WHEN {field}='UNKNOWN_FIELD'
                 AND {section}='UNKNOWN_SECTION'
                 AND {account}='' THEN
                    'APPROPRIATION|' || {p}.fiscal_year || '|' || {org} ||
                    '|UNSTRUCTURED|' || {p}.raw_source_key
                ELSE
                    'APPROPRIATION|' || {p}.fiscal_year || '|' || {org} || '|' ||
                    {field} || '|' || {section} || '|' || {account}
            END
        ELSE {p}.source_layer || '|' || {p}.fiscal_year || '|' || {p}.raw_dataset || '|' || {p}.raw_source_key
    END"""


def _identity_from_fact(fact, *, raw_source_key="", source_operation="", source_system=""):
    """Mirror _identity_sql for a projected revision payload."""
    def first(*values):
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    layer = str(fact.get("source_layer") or "")
    year = int(fact.get("fiscal_year") or 0)
    org = first(
        fact.get("org_code"), fact.get("org_name"),
        fact.get("region_code"), fact.get("region_name"),
        "UNKNOWN_ORG",
    )
    project = first(
        fact.get("project_code"), fact.get("project_name"), raw_source_key
    )
    department = first(fact.get("dept_code"), fact.get("dept_name"))
    institution = first(
        fact.get("institution_code"), fact.get("institution_name")
    )
    account = first(fact.get("account_code"), fact.get("account_name"))
    field = first(
        fact.get("field_code"), fact.get("field_name"), "UNKNOWN_FIELD"
    )
    section = first(
        fact.get("section_code"), fact.get("section_name"), "UNKNOWN_SECTION"
    )
    operation = str(source_operation or "")
    if ":" in operation:
        education_partition = operation.split(":", 1)[1]
    else:
        education_partition = first(
            operation, source_system, "UNKNOWN_EDUCATION_SOURCE"
        )

    if layer == "DETAIL_EXECUTION":
        return f"DETAIL_EXECUTION|{year}|{org}|{department}|{project}|{account}"
    if layer == "EDUCATION":
        return (
            f"EDUCATION|{year}|{org}|{institution}|{department}|{project}|"
            f"{education_partition}|{account}"
        )
    if layer == "APPROPRIATION":
        if field == "UNKNOWN_FIELD" and section == "UNKNOWN_SECTION" and not account:
            return f"APPROPRIATION|{year}|{org}|UNSTRUCTURED|{raw_source_key}"
        return f"APPROPRIATION|{year}|{org}|{field}|{section}|{account}"
    return f"{layer}|{year}|{raw_source_key}"


def _current_cte(where_sql="", *, alias="p"):
    identity = _identity_sql(alias)
    return f"""
        WITH base AS (
            SELECT {alias}.*, {identity} AS project_identity
            FROM vnext_budget_projection {alias}
            JOIN raw_records current_raw
              ON current_raw.dataset={alias}.raw_dataset
             AND current_raw.source_key={alias}.raw_source_key
             AND current_raw.payload_sha256={alias}.payload_sha256
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
    Only projections matching the current stored RAW payload hash are eligible, so a
    stale projection cannot be exposed as the current budget state.
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


def _stable_source_revision_timeline(identity, expr, *, dataset, source_layer):
    """Expand immutable revisions that belong to the requested identity.

    Historical source-key collisions must not leak revisions into the current
    project's timeline, but a later payload moving that source key to a different
    project must not make the former project's immutable history unreachable.
    Recompute identity from revision payloads themselves and use current RAW only to
    mark which revision is current.
    """
    parts = str(identity or "").split("|", 2)
    identity_year = parts[1] if len(parts) > 1 and parts[1].isdigit() else ""

    where = "r.dataset=?"
    params = [str(dataset)]
    if identity_year:
        # All vNext budget collectors bind source_date to the requested fiscal year
        # (or a date inside it). Restrict the revision scan to that year without
        # depending on the current projection identity.
        where += " AND r.source_date LIKE ?"
        params.append(identity_year + "%")

    with connect() as conn:
        revisions = conn.execute(
            f"""SELECT r.id,r.source_system,r.source_operation,r.source_key,
                       r.source_date,r.fetched_at,r.payload_json,r.payload_sha256,
                       current_raw.payload_sha256 AS current_payload_sha256
                FROM raw_record_revisions r
                LEFT JOIN raw_records current_raw
                  ON current_raw.dataset=r.dataset
                 AND current_raw.source_key=r.source_key
                WHERE {where}
                ORDER BY r.source_date,r.fetched_at,r.id""",
            tuple(params),
        ).fetchall()

    result = []
    for revision in revisions:
        payload = json.loads(revision["payload_json"] or "{}")
        fact = budget_projection_vnext.project_payload(
            str(dataset), payload, source_date=revision["source_date"]
        )
        if str(fact.get("source_layer") or "") != str(source_layer):
            continue
        revision_identity = _identity_from_fact(
            fact,
            raw_source_key=str(revision["source_key"]),
            source_operation=str(revision["source_operation"] or ""),
            source_system=str(revision["source_system"] or ""),
        )
        if revision_identity != identity:
            continue
        result.append({
            "raw_dataset": str(dataset),
            "raw_source_key": str(revision["source_key"]),
            "source_system": str(revision["source_system"] or ""),
            "source_operation": str(revision["source_operation"] or ""),
            **fact,
            "amounts_json": json.dumps(
                budget_projection_vnext._amount_fields(payload),
                ensure_ascii=False, sort_keys=True, default=str,
            ),
            "payload_sha256": str(revision["payload_sha256"] or ""),
            "updated_at": str(revision["fetched_at"] or ""),
            "project_identity": identity,
            "revision_id": int(revision["id"]),
            "revision_fetched_at": str(revision["fetched_at"] or ""),
            "is_current_revision": (
                str(revision["current_payload_sha256"] or "")
                == str(revision["payload_sha256"] or "")
            ),
        })
    return result


def budget_timeline(project_identity):
    """Return preserved history for one stable project identity.

    Every supported layer expands immutable raw_record_revisions. QWGJK may have
    multiple date-bearing source keys for one project identity, and each snapshot key
    may itself receive source corrections; both dimensions of history are retained.
    """
    budget_projection_vnext.ensure_schema()
    identity = str(project_identity or "").strip()
    if not identity:
        return []
    expr = _identity_sql("p")
    if identity.startswith("DETAIL_EXECUTION|"):
        return _stable_source_revision_timeline(
            identity, expr,
            dataset="budget",
            source_layer="DETAIL_EXECUTION",
        )
    if identity.startswith("EDUCATION|"):
        return _stable_source_revision_timeline(
            identity, expr,
            dataset="education_budget",
            source_layer="EDUCATION",
        )
    if identity.startswith("APPROPRIATION|"):
        return _stable_source_revision_timeline(
            identity, expr,
            dataset="budget_appropriation",
            source_layer="APPROPRIATION",
        )
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
    """Return current conservative structural context matches from AIDFA to QWGJK.

    Only the latest/current row for each organized appropriation/detail identity
    participates, and each projection must match the current stored RAW payload hash.
    Historical QWGJK snapshots remain available through timelines but do not multiply
    the current structural-context relation.

    A relation requires exact fiscal year and organization plus field/section/account
    identity. Codes are preferred whenever both sides provide them; otherwise exact
    non-empty names are required. This is hierarchy/context only, not a one-to-one
    project-budget claim.
    """
    budget_projection_vnext.ensure_schema()
    a_identity = _identity_sql("a")
    d_identity = _identity_sql("d")
    filters = [
        "a._rn=1",
        "d._rn=1",
        "a.fiscal_year=d.fiscal_year",
        "TRIM(a.org_code)<>''",
        "a.org_code=d.org_code",
        """(
            (TRIM(a.field_code)<>'' AND TRIM(d.field_code)<>''
             AND a.field_code=d.field_code)
            OR
            ((TRIM(a.field_code)='' OR TRIM(d.field_code)='')
             AND TRIM(a.field_name)<>'' AND TRIM(d.field_name)<>''
             AND a.field_name=d.field_name)
        )""",
        """(
            (TRIM(a.section_code)<>'' AND TRIM(d.section_code)<>''
             AND a.section_code=d.section_code)
            OR
            ((TRIM(a.section_code)='' OR TRIM(d.section_code)='')
             AND TRIM(a.section_name)<>'' AND TRIM(d.section_name)<>''
             AND a.section_name=d.section_name)
        )""",
        """(
            (TRIM(a.account_code)<>'' AND TRIM(d.account_code)<>''
             AND a.account_code=d.account_code)
            OR
            ((TRIM(a.account_code)='' OR TRIM(d.account_code)='')
             AND TRIM(a.account_name)<>'' AND TRIM(d.account_name)<>''
             AND a.account_name=d.account_name)
        )""",
    ]
    params = []
    if fiscal_year is not None:
        filters.append("a.fiscal_year=?")
        params.append(int(fiscal_year))
    where = " AND ".join(filters)

    with connect() as conn:
        rows = conn.execute(
            f"""WITH
                appropriation_base AS (
                    SELECT a.*, {a_identity} AS project_identity
                    FROM vnext_budget_projection a
                    JOIN raw_records appropriation_raw
                      ON appropriation_raw.dataset=a.raw_dataset
                     AND appropriation_raw.source_key=a.raw_source_key
                     AND appropriation_raw.payload_sha256=a.payload_sha256
                    WHERE a.source_layer='APPROPRIATION'
                ),
                appropriation_ranked AS (
                    SELECT appropriation_base.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY project_identity
                               ORDER BY
                                   CASE WHEN COALESCE(snapshot_date,'')=''
                                        THEN '0000-00-00' ELSE snapshot_date END DESC,
                                   updated_at DESC,
                                   raw_source_key DESC
                           ) AS _rn
                    FROM appropriation_base
                ),
                detail_base AS (
                    SELECT d.*, {d_identity} AS project_identity
                    FROM vnext_budget_projection d
                    JOIN raw_records detail_raw
                      ON detail_raw.dataset=d.raw_dataset
                     AND detail_raw.source_key=d.raw_source_key
                     AND detail_raw.payload_sha256=d.payload_sha256
                    WHERE d.source_layer='DETAIL_EXECUTION'
                ),
                detail_ranked AS (
                    SELECT detail_base.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY project_identity
                               ORDER BY
                                   CASE WHEN COALESCE(snapshot_date,'')=''
                                        THEN '0000-00-00' ELSE snapshot_date END DESC,
                                   updated_at DESC,
                                   raw_source_key DESC
                           ) AS _rn
                    FROM detail_base
                )
                SELECT DISTINCT
                       a.project_identity AS appropriation_identity,
                       d.project_identity AS detail_identity,
                       a.fiscal_year,a.org_code,a.org_name,
                       a.budget_amount AS appropriation_budget_amount,
                       a.appropriation_amount AS appropriation_amount,
                       a.field_code AS appropriation_field_code,
                       a.field_name,
                       d.field_code AS detail_field_code,
                       a.section_code AS appropriation_section_code,
                       a.section_name,
                       d.section_code AS detail_section_code,
                       a.account_code AS appropriation_account_code,
                       a.account_name AS appropriation_account_name,
                       d.account_code AS detail_account_code,
                       d.account_name AS detail_account_name,
                       d.dept_code AS detail_dept_code,
                       d.dept_name AS detail_dept_name,
                       d.project_code AS detail_project_code,
                       d.project_name AS detail_project_name,
                       d.snapshot_date AS detail_snapshot_date,
                       d.budget_amount AS detail_budget_amount,
                       d.executed_amount AS detail_executed_amount,
                       d.remaining_amount AS detail_remaining_amount,
                       a.raw_source_key AS appropriation_raw_key,
                       d.raw_source_key AS detail_raw_key,
                       CASE
                         WHEN TRIM(a.account_code)<>'' AND TRIM(d.account_code)<>''
                           THEN 'EXACT_ORG_FIELD_SECTION_ACCOUNT_CODE'
                         ELSE 'EXACT_ORG_FIELD_SECTION_ACCOUNT_NAME'
                       END AS match_basis,
                       CASE
                         WHEN TRIM(a.field_code)<>'' AND TRIM(d.field_code)<>''
                           THEN 'CODE'
                         ELSE 'NAME'
                       END AS field_match_basis,
                       CASE
                         WHEN TRIM(a.section_code)<>'' AND TRIM(d.section_code)<>''
                           THEN 'CODE'
                         ELSE 'NAME'
                       END AS section_match_basis,
                       1.0 AS confidence
                FROM appropriation_ranked a
                JOIN detail_ranked d ON {where}
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
