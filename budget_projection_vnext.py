"""Canonical organization layer for all vNext budget RAW sources.

The goal is not to replace the existing AI-SPECE budget serving tables. This module
creates one additive projection over three preserved RAW layers:

* budget               -> LOFIN/QWGJK detail-business execution snapshots
* budget_appropriation -> LOFIN/AIDFA appropriation structure
* education_budget     -> 지방교육재정알리미 full RAW rows

Every RAW row remains available and classification stays post-collection.
"""
from __future__ import annotations

import json

import budget_storage
from db import connect
from vnext_store import ensure_foundation

DATASETS = ("budget", "budget_appropriation", "education_budget")

SCHEMA = r'''
CREATE TABLE IF NOT EXISTS vnext_budget_projection (
    raw_dataset TEXT NOT NULL,
    raw_source_key TEXT NOT NULL,
    source_system TEXT NOT NULL DEFAULT '',
    source_operation TEXT NOT NULL DEFAULT '',
    source_layer TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL DEFAULT 0,
    snapshot_date TEXT NOT NULL DEFAULT '',
    region_code TEXT NOT NULL DEFAULT '',
    region_name TEXT NOT NULL DEFAULT '',
    org_code TEXT NOT NULL DEFAULT '',
    org_name TEXT NOT NULL DEFAULT '',
    dept_code TEXT NOT NULL DEFAULT '',
    dept_name TEXT NOT NULL DEFAULT '',
    institution_code TEXT NOT NULL DEFAULT '',
    institution_name TEXT NOT NULL DEFAULT '',
    project_code TEXT NOT NULL DEFAULT '',
    project_name TEXT NOT NULL DEFAULT '',
    field_code TEXT NOT NULL DEFAULT '',
    field_name TEXT NOT NULL DEFAULT '',
    section_code TEXT NOT NULL DEFAULT '',
    section_name TEXT NOT NULL DEFAULT '',
    account_code TEXT NOT NULL DEFAULT '',
    account_name TEXT NOT NULL DEFAULT '',
    budget_amount INTEGER NOT NULL DEFAULT 0,
    appropriation_amount INTEGER NOT NULL DEFAULT 0,
    executed_amount INTEGER NOT NULL DEFAULT 0,
    remaining_amount INTEGER NOT NULL DEFAULT 0,
    national_amount INTEGER NOT NULL DEFAULT 0,
    province_amount INTEGER NOT NULL DEFAULT 0,
    local_amount INTEGER NOT NULL DEFAULT 0,
    other_amount INTEGER NOT NULL DEFAULT 0,
    amounts_json TEXT NOT NULL DEFAULT '{}',
    payload_sha256 TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(raw_dataset, raw_source_key)
);
CREATE INDEX IF NOT EXISTS ix_vnext_budget_projection_year_layer
    ON vnext_budget_projection(fiscal_year, source_layer);
CREATE INDEX IF NOT EXISTS ix_vnext_budget_projection_org
    ON vnext_budget_projection(org_name, fiscal_year);
CREATE INDEX IF NOT EXISTS ix_vnext_budget_projection_project
    ON vnext_budget_projection(project_name, fiscal_year);
'''


def _ensure_column(conn, table, column, ddl):
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def ensure_schema():
    ensure_foundation()
    with connect() as conn:
        conn.executescript(SCHEMA)
        _ensure_column(
            conn,
            "vnext_budget_projection",
            "account_code",
            "TEXT NOT NULL DEFAULT ''",
        )
        _ensure_column(
            conn,
            "vnext_budget_projection",
            "field_code",
            "TEXT NOT NULL DEFAULT ''",
        )
        _ensure_column(
            conn,
            "vnext_budget_projection",
            "section_code",
            "TEXT NOT NULL DEFAULT ''",
        )
        _ensure_column(
            conn,
            "vnext_budget_projection",
            "institution_code",
            "TEXT NOT NULL DEFAULT ''",
        )
        _ensure_column(
            conn,
            "vnext_budget_projection",
            "institution_name",
            "TEXT NOT NULL DEFAULT ''",
        )


def _norm_key(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _pick(row, *names):
    if not isinstance(row, dict):
        return ""
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    normalized = {_norm_key(k): v for k, v in row.items()}
    for name in names:
        value = normalized.get(_norm_key(name))
        if value not in (None, ""):
            return value
    return ""


def _num(value, default=0):
    try:
        text = str(value or "").replace(",", "").replace("원", "").strip()
        return int(round(float(text))) if text else int(default)
    except (TypeError, ValueError):
        return int(default)


def _year(row, fallback=""):
    return _num(_pick(row, "fyr", "YMQ", "year", "fiscalYear", "회계연도"), _num(fallback, 0))


def _date_text(value):
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(text) == 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return str(value or "").strip()


def _amount_fields(row):
    """Retain all amount-like source fields so unknown source semantics are not lost."""
    result = {}
    for key, value in (row or {}).items():
        key_text = str(key)
        norm = _norm_key(key_text)
        if any(token in norm for token in ("amt", "amount", "bdg", "budget", "ep", "예산", "집행", "지출", "결산", "편성")):
            if value not in (None, ""):
                result[key_text] = value
    return result


def _project_qwgjk(row, source_date):
    budget_source = _pick(row, "bdg_cash_amt", "budget_amount", "예산현액")
    appropriation_source = _pick(row, "cpl_amt", "compile_amt", "편성액")
    budget = _num(budget_source)
    appropriation = _num(appropriation_source)
    executed = _num(_pick(row, "ep_amt", "executed_amount", "지출액", "집행액"))
    has_current_budget = budget_source not in (None, "")
    has_appropriation = appropriation_source not in (None, "")
    basis = budget if has_current_budget else appropriation
    return {
        "source_layer": "DETAIL_EXECUTION",
        "fiscal_year": _year(row, str(source_date)[:4]),
        # In PostgreSQL mode one stable observation may be re-seen on a newer
        # collection date without creating a duplicate RAW revision.  source_date is
        # therefore the authoritative current snapshot date; exe_ymd remains inside
        # the preserved source payload for audit.
        "snapshot_date": _date_text(source_date or _pick(row, "exe_ymd")),
        "region_code": str(_pick(row, "wa_laf_cd") or ""),
        "region_name": str(_pick(row, "wa_laf_hg_nm") or ""),
        "org_code": str(_pick(row, "laf_cd") or ""),
        "org_name": str(_pick(row, "laf_hg_nm") or _pick(row, "wa_laf_hg_nm") or ""),
        "dept_code": str(_pick(row, "dept_cd") or ""),
        "dept_name": str(_pick(row, "dept_nm") or ""),
        "institution_code": "",
        "institution_name": "",
        "project_code": str(_pick(row, "dbiz_cd") or ""),
        "project_name": str(_pick(row, "dbiz_nm") or ""),
        "field_code": str(_pick(row, "fld_cd") or ""),
        "field_name": str(_pick(row, "fld_nm") or ""),
        "section_code": str(_pick(row, "ane_part_cd", "part_cd", "sect_cd") or ""),
        "section_name": str(_pick(row, "part_nm", "sect_nm") or ""),
        "account_code": str(_pick(row, "acnt_dv_cd") or ""),
        "account_name": str(_pick(row, "acnt_dv_nm") or ""),
        "budget_amount": budget,
        "appropriation_amount": appropriation,
        "executed_amount": executed,
        "remaining_amount": (
            basis - executed
            if (has_current_budget or has_appropriation)
            else 0
        ),
        "national_amount": _num(_pick(row, "bdg_ntep")),
        "province_amount": _num(_pick(row, "capep")),
        "local_amount": _num(_pick(row, "sggep")),
        "other_amount": _num(_pick(row, "etc_amt")),
    }


def _project_appropriation(row, source_date):
    """Project the documented AIDFA structural budget fields.

    AIDFA exposes structural totals, not detail-business rows. The canonical amount
    uses 정책사업예산총계 (biz_bdg_tott_amt), with 정책사업예산순계 as a fallback.
    Every source amount column is still preserved verbatim in amounts_json.
    """
    policy_budget_total = _num(_pick(row, "biz_bdg_tott_amt", "biz_bdg_prsm_amt"))
    field_name = str(_pick(row, "fld_nm", "field_name") or "")
    section_name = str(_pick(row, "sect_nm", "part_nm", "section_name") or "")
    project_name = " > ".join(part for part in (field_name, section_name) if part)
    return {
        "source_layer": "APPROPRIATION",
        "fiscal_year": _year(row, str(source_date)[:4]),
        "snapshot_date": "",
        "region_code": str(_pick(row, "wa_laf_cd") or ""),
        "region_name": str(_pick(row, "wa_laf_hg_nm") or ""),
        "org_code": str(_pick(row, "laf_cd") or _pick(row, "wa_laf_cd") or ""),
        "org_name": str(_pick(row, "laf_hg_nm") or _pick(row, "wa_laf_hg_nm") or ""),
        "dept_code": "",
        "dept_name": "",
        "institution_code": "",
        "institution_name": "",
        "project_code": "",
        "project_name": project_name,
        "field_code": str(_pick(row, "fld_cd") or ""),
        "field_name": field_name,
        "section_code": str(_pick(row, "sect_cd", "part_cd") or ""),
        "section_name": section_name,
        "account_code": str(_pick(row, "acnt_dv_cd") or ""),
        "account_name": str(_pick(row, "acnt_dv_nm") or ""),
        "budget_amount": policy_budget_total,
        "appropriation_amount": policy_budget_total,
        "executed_amount": 0,
        "remaining_amount": policy_budget_total,
        "national_amount": 0,
        "province_amount": 0,
        "local_amount": 0,
        "other_amount": 0,
    }


def _project_education(row, source_date):
    budget_source = _pick(
        row, "budget_amount", "budgetAmount", "bdgAmt", "BUDGET_AMT",
        "예산액", "예산현액", "본예산액", "최종예산액",
    )
    budget = _num(budget_source)
    has_budget = budget_source not in (None, "")
    executed = _num(_pick(
        row, "executed_amount", "executedAmount", "expenseAmt", "EXPENDITURE_AMT",
        "집행액", "지출액", "결산액",
    ))
    office = str(_pick(
        row, "office_name", "officeName", "eduOfficeNm", "ATPT_OFCDC_SC_NM",
        "시도교육청명", "교육청명", "기관명", "org_name",
    ) or "")
    return {
        "source_layer": "EDUCATION",
        "fiscal_year": _year(row, str(source_date)[:4]),
        "snapshot_date": _date_text(source_date),
        "region_code": "",
        "region_name": office,
        "org_code": str(_pick(row, "officeCode", "eduOfficeCode", "ATPT_OFCDC_SC_CODE", "교육청코드") or ""),
        "org_name": office,
        "dept_code": str(_pick(row, "departmentCode", "deptCode", "부서코드") or ""),
        "dept_name": str(_pick(row, "departmentName", "deptName", "부서명") or ""),
        "institution_code": str(_pick(
            row, "schoolCode", "institutionCode", "학교코드", "기관코드"
        ) or ""),
        "institution_name": str(_pick(
            row, "schoolName", "institutionName", "학교명", "기관명"
        ) or ""),
        "project_code": str(_pick(row, "projectCode", "businessCode", "사업코드", "세부사업코드") or ""),
        "project_name": str(_pick(
            row, "project_name", "projectName", "business_name", "businessName", "bizNm", "bsnsNm",
            "dbiz_nm", "SAUP_NM", "사업명", "세부사업명", "단위사업명", "정책사업명",
        ) or ""),
        "field_code": "",
        "field_name": "교육비특별회계",
        "section_code": str(_pick(row, "programCode", "policyBusinessCode", "정책사업코드", "단위사업코드") or ""),
        "section_name": str(_pick(row, "programName", "policyBusinessName", "정책사업명", "단위사업명") or ""),
        "account_code": str(_pick(row, "accountCode", "itemCode", "세목코드", "과목코드") or ""),
        "account_name": str(_pick(row, "accountName", "itemName", "세목명", "과목명") or ""),
        "budget_amount": budget,
        "appropriation_amount": budget,
        "executed_amount": executed,
        "remaining_amount": budget - executed if has_budget else 0,
        "national_amount": 0,
        "province_amount": 0,
        "local_amount": 0,
        "other_amount": 0,
    }


def project_payload(dataset, payload, *, source_date=""):
    payload = payload if isinstance(payload, dict) else {}
    if dataset == "budget":
        return _project_qwgjk(payload, source_date)
    if dataset == "budget_appropriation":
        return _project_appropriation(payload, source_date)
    if dataset == "education_budget":
        return _project_education(payload, source_date)
    raise ValueError(f"unsupported budget dataset: {dataset}")


def _prune_stale_read_model(conn, selected, current_hashes, *, batch_size=1000):
    """Remove stale SQLite read-model rows in bounded delete batches."""
    placeholders = ",".join("?" for _ in selected)
    size = max(1, min(int(batch_size), 5000))
    deleted_projection_rows = 0
    deleted_classification_rows = 0

    last_rowid = 0
    while True:
        rows = conn.execute(
            f"""SELECT rowid AS _rowid,raw_dataset,raw_source_key,payload_sha256
                FROM vnext_budget_projection
                WHERE raw_dataset IN ({placeholders}) AND rowid>?
                ORDER BY rowid LIMIT ?""",
            (*selected, last_rowid, size),
        ).fetchall()
        if not rows:
            break
        last_rowid = max(int(row["_rowid"]) for row in rows)
        stale = [
            (int(row["_rowid"]),)
            for row in rows
            if current_hashes.get(
                (str(row["raw_dataset"]), str(row["raw_source_key"]))
            ) != str(row["payload_sha256"] or "")
        ]
        if stale:
            conn.executemany(
                "DELETE FROM vnext_budget_projection WHERE rowid=?",
                stale,
            )
            deleted_projection_rows += len(stale)

    last_id = 0
    while True:
        rows = conn.execute(
            f"""SELECT id,entity_type,entity_key,source_payload_sha256
                FROM classifications
                WHERE entity_type IN ({placeholders}) AND id>?
                ORDER BY id LIMIT ?""",
            (*selected, last_id, size),
        ).fetchall()
        if not rows:
            break
        last_id = max(int(row["id"]) for row in rows)
        stale = [
            (int(row["id"]),)
            for row in rows
            if current_hashes.get(
                (str(row["entity_type"]), str(row["entity_key"]))
            ) != str(row["source_payload_sha256"] or "")
        ]
        if stale:
            conn.executemany(
                "DELETE FROM classifications WHERE id=?",
                stale,
            )
            deleted_classification_rows += len(stale)

    return {
        "deleted_projection_rows": deleted_projection_rows,
        "deleted_classification_rows": deleted_classification_rows,
    }


def prune_stale_budget_read_model(*, datasets=None):
    """Synchronize SQLite budget read-model rows to exact current stored RAW only."""
    ensure_schema()
    selected = tuple(datasets or DATASETS)
    unknown = set(selected) - set(DATASETS)
    if unknown:
        raise ValueError("unsupported budget datasets: " + ", ".join(sorted(unknown)))

    current_hashes = budget_storage.current_payload_hashes(selected)
    with connect() as conn:
        return {
            **_prune_stale_read_model(conn, selected, current_hashes),
            "current_raw_rows": len(current_hashes),
            "datasets": list(selected),
        }


def _projection_values(raw):
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        try:
            payload = json.loads(raw.get("payload_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
    fact = project_payload(
        raw["dataset"], payload, source_date=raw.get("source_date") or ""
    )
    amounts_json = json.dumps(
        _amount_fields(payload),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return (
        raw["dataset"], raw["source_key"], raw["source_system"], raw["source_operation"],
        fact["source_layer"], fact["fiscal_year"], fact["snapshot_date"],
        fact["region_code"], fact["region_name"], fact["org_code"], fact["org_name"],
        fact["dept_code"], fact["dept_name"], fact["institution_code"], fact["institution_name"],
        fact["project_code"], fact["project_name"],
        fact["field_code"], fact["field_name"], fact["section_code"], fact["section_name"],
        fact["account_code"], fact["account_name"],
        fact["budget_amount"], fact["appropriation_amount"], fact["executed_amount"],
        fact["remaining_amount"], fact["national_amount"], fact["province_amount"],
        fact["local_amount"], fact["other_amount"], amounts_json, raw["payload_sha256"],
    )


_PROJECTION_UPSERT_SQL = """
    INSERT INTO vnext_budget_projection(
        raw_dataset,raw_source_key,source_system,source_operation,source_layer,
        fiscal_year,snapshot_date,region_code,region_name,org_code,org_name,
        dept_code,dept_name,institution_code,institution_name,
        project_code,project_name,field_code,field_name,section_code,section_name,account_code,account_name,
        budget_amount,appropriation_amount,executed_amount,remaining_amount,
        national_amount,province_amount,local_amount,other_amount,amounts_json,payload_sha256
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(raw_dataset,raw_source_key) DO UPDATE SET
        source_system=excluded.source_system,source_operation=excluded.source_operation,
        source_layer=excluded.source_layer,fiscal_year=excluded.fiscal_year,
        snapshot_date=excluded.snapshot_date,region_code=excluded.region_code,
        region_name=excluded.region_name,org_code=excluded.org_code,org_name=excluded.org_name,
        dept_code=excluded.dept_code,dept_name=excluded.dept_name,
        institution_code=excluded.institution_code,institution_name=excluded.institution_name,
        project_code=excluded.project_code,project_name=excluded.project_name,
        field_code=excluded.field_code,field_name=excluded.field_name,
        section_code=excluded.section_code,section_name=excluded.section_name,
        account_code=excluded.account_code,account_name=excluded.account_name,
        budget_amount=excluded.budget_amount,
        appropriation_amount=excluded.appropriation_amount,
        executed_amount=excluded.executed_amount,remaining_amount=excluded.remaining_amount,
        national_amount=excluded.national_amount,province_amount=excluded.province_amount,
        local_amount=excluded.local_amount,other_amount=excluded.other_amount,
        amounts_json=excluded.amounts_json,payload_sha256=excluded.payload_sha256,
        updated_at=CURRENT_TIMESTAMP
"""


def refresh_budget_projection(*, datasets=None, batch_size=1000):
    """Upsert exact-current canonical rows in bounded batches.

    PostgreSQL payload JSON is streamed a batch at a time. This keeps first-run
    projection of large budget datasets from materializing every source payload in
    the web process at once.
    """
    ensure_schema()
    selected = tuple(datasets or DATASETS)
    unknown = set(selected) - set(DATASETS)
    if unknown:
        raise ValueError("unsupported budget datasets: " + ", ".join(sorted(unknown)))

    size = max(1, min(int(batch_size), 5000))
    counts = {name: 0 for name in selected}
    current_hashes = budget_storage.current_payload_hashes(selected)
    with connect() as conn:
        pruned = _prune_stale_read_model(conn, selected, current_hashes)

    for batch in budget_storage.current_raw_batches(
        selected, batch_size=size
    ):
        prepared = [_projection_values(raw) for raw in batch]
        if not prepared:
            continue
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(_PROJECTION_UPSERT_SQL, prepared)
        for raw in batch:
            counts[str(raw["dataset"])] += 1

    return {
        "projected": sum(counts.values()),
        "by_dataset": counts,
        "deleted_projection_rows": int(pruned["deleted_projection_rows"]),
        "deleted_classification_rows": int(pruned["deleted_classification_rows"]),
        "batch_size": size,
    }


def budget_overview(fiscal_year=None):
    """Return current-RAW source-layer counts/amounts for diagnostics/UI.

    Stale projection rows are intentionally excluded until they are refreshed from
    the current stored RAW payload.
    """
    ensure_schema()
    where = ""
    params = ()
    if fiscal_year is not None:
        where = "WHERE fiscal_year=?"
        params = (int(fiscal_year),)
    if budget_storage.using_postgres():
        current = budget_storage.current_payload_hashes(DATASETS)
        clauses = []
        values = []
        if fiscal_year is not None:
            clauses.append("fiscal_year=?")
            values.append(int(fiscal_year))
        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM vnext_budget_projection {where_sql}
                    ORDER BY source_layer,raw_dataset,raw_source_key""",
                tuple(values),
            ).fetchall()
        grouped = {}
        for row in rows:
            item = dict(row)
            key = (str(item["raw_dataset"]), str(item["raw_source_key"]))
            if current.get(key, "") != str(item.get("payload_sha256") or ""):
                continue
            layer = str(item.get("source_layer") or "")
            slot = grouped.setdefault(layer, {
                "source_layer": layer, "rows": 0, "budget_amount": 0,
                "executed_amount": 0, "remaining_amount": 0,
            })
            slot["rows"] += 1
            slot["budget_amount"] += int(item.get("budget_amount") or 0)
            slot["executed_amount"] += int(item.get("executed_amount") or 0)
            slot["remaining_amount"] += int(item.get("remaining_amount") or 0)
        return [grouped[key] for key in sorted(grouped)]

    with connect() as conn:
        rows = conn.execute(
            f"""SELECT source_layer,COUNT(*) AS rows,
                       SUM(budget_amount) AS budget_amount,
                       SUM(executed_amount) AS executed_amount,
                       SUM(remaining_amount) AS remaining_amount
                FROM vnext_budget_projection p
                JOIN raw_records r
                  ON r.dataset=p.raw_dataset
                 AND r.source_key=p.raw_source_key
                 AND r.payload_sha256=p.payload_sha256
                {where}
                GROUP BY source_layer ORDER BY source_layer""",
            params,
        ).fetchall()
    return [dict(row) for row in rows]
