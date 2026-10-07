"""Compact result snapshots for the Cafe24 result-server role.

The local collector retains source RAW and immutable revisions. Cafe24 receives only
screen/API result rows plus compact status metadata. No source payload JSON is copied
unless a field is already part of a rendered analysis row.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

from db import current_db_path

SNAPSHOT_SCHEMA_VERSION = 1
MAX_SNAPSHOT_ROWS = 500_000
PRODUCTION_SERVING_DB_PATH = "/app/user_data/g2b-serving.sqlite3"

SERVING_SCHEMA = """
CREATE TABLE IF NOT EXISTS serving_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS serving_rows(
    snapshot_id TEXT NOT NULL,
    section TEXT NOT NULL,
    row_key TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    fiscal_year TEXT NOT NULL DEFAULT '',
    search_text TEXT NOT NULL DEFAULT '',
    sort_num REAL NOT NULL DEFAULT 0,
    sort_text TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_id,section,row_key)
);
CREATE INDEX IF NOT EXISTS ix_serving_rows_section
    ON serving_rows(snapshot_id,section,sort_num DESC,sort_text DESC);
CREATE INDEX IF NOT EXISTS ix_serving_rows_category
    ON serving_rows(snapshot_id,section,category);
CREATE INDEX IF NOT EXISTS ix_serving_rows_year
    ON serving_rows(snapshot_id,section,fiscal_year);
CREATE INDEX IF NOT EXISTS ix_serving_rows_shopping_date
    ON serving_rows(snapshot_id,section,sort_text DESC);
"""


def serving_db_path():
    configured = str(os.getenv("G2B_SERVING_DB_PATH", "") or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))

    # Test/local SQLite keeps the compact serving DB beside the local fixture.
    # Production current_db_path() is a logical PostgreSQL locator such as
    # "postgresql://configured/g2b_app", not a filesystem path. Never feed that
    # value through os.path.dirname(), which previously produced the invalid
    # relative path "postgresql:/configured/g2b-serving.sqlite3".
    source_path = str(current_db_path() or "").strip()
    if "://" not in source_path:
        return os.path.join(
            os.path.dirname(os.path.abspath(os.path.expanduser(source_path))),
            "g2b-serving.sqlite3",
        )
    return PRODUCTION_SERVING_DB_PATH


@contextmanager
def _connect():
    path = serving_db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=3.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_schema():
    with _connect() as conn:
        conn.executescript(SERVING_SCHEMA)


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _canonical(value):
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _row_key(section, row):
    if section == "shopping":
        return str(row.get("source_key") or _hash(row))
    if section == "vendors":
        identity = f"{row.get('vendor_bizno','')}|{row.get('vendor_name','')}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return _hash(row)


def _index_fields(section, row):
    category = str(row.get("primary_category") or "").upper()
    fiscal_year = str(row.get("fiscal_year") or "")
    search_parts = []
    for value in row.values():
        if isinstance(value, (str, int, float)):
            search_parts.append(str(value))
    search_text = " | ".join(search_parts).casefold()
    if section == "vendors":
        sort_num = float(row.get("total_amount") or 0)
        sort_text = str(row.get("vendor_name") or "")
    elif section.startswith("budget_"):
        sort_num = float(row.get("remaining_amount") or row.get("budget_amount") or 0)
        sort_text = str(row.get("fiscal_year") or "")
    else:
        sort_num = float(row.get("amount") or 0)
        sort_text = str(row.get("source_date") or row.get("fetched_at") or "")
    return category, fiscal_year, search_text[:12000], sort_num, sort_text



def clear_snapshot():
    """Delete compatibility serving data; auth/settings live in a different DB."""
    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM serving_rows")
        conn.execute("DELETE FROM serving_meta")
    return {"cleared": True}


def active_snapshot_id():
    """Read snapshot identity without creating files/directories or taking writes."""
    path = serving_db_path()
    if not os.path.isfile(path):
        return ""
    try:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=0.25)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT value FROM serving_meta WHERE key='active_snapshot_id'"
            ).fetchone()
        finally:
            conn.close()
        return str(row["value"] if row else "")
    except (OSError, ValueError, sqlite3.Error):
        return ""


def snapshot_available():
    return bool(active_snapshot_id())


def snapshot_metadata():
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute("SELECT key,value FROM serving_meta").fetchall()
    data = {str(row["key"]): str(row["value"]) for row in rows}
    for key in ("manifest_json", "collection_status_json", "readiness_json", "source_counts_json"):
        if key in data:
            try:
                data[key[:-5] if key.endswith("_json") else key] = json.loads(data[key])
            except (TypeError, ValueError):
                data[key[:-5] if key.endswith("_json") else key] = {}
    return data


def import_snapshot(payload):
    if not isinstance(payload, dict):
        raise ValueError("snapshot must be an object")
    if int(payload.get("schema_version") or 0) != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported snapshot schema")
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("snapshot sections required")
    total_rows = sum(len(v) for v in sections.values() if isinstance(v, list))
    if total_rows > MAX_SNAPSHOT_ROWS:
        raise ValueError("snapshot row limit exceeded")
    if any(not isinstance(v, list) for v in sections.values()):
        raise ValueError("every snapshot section must be a list")

    # json.loads already produces JSON-native dict/list/scalar values.
    # Keeping a second recursive copy of the whole snapshot can nearly double
    # peak memory on the result server. Individual rows/meta are canonicalized
    # when written, so retain the original parsed object instead.
    safe = payload
    snapshot_id = str(payload.get("snapshot_id") or _hash({
        "generated_at_utc": payload.get("generated_at_utc"),
        "source_version": payload.get("source_version"),
        "counts": {k: len(v) for k, v in sections.items()},
    }))
    ensure_schema()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM serving_rows WHERE snapshot_id=?", (snapshot_id,))
        for section, rows in safe["sections"].items():
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("snapshot row must be an object")
                key = _row_key(section, row)
                category, fiscal_year, search_text, sort_num, sort_text = _index_fields(section, row)
                conn.execute(
                    """INSERT INTO serving_rows(
                        snapshot_id,section,row_key,category,fiscal_year,search_text,
                        sort_num,sort_text,payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        snapshot_id, str(section), key, category, fiscal_year,
                        search_text, sort_num, sort_text, _canonical(row),
                    ),
                )
        manifest = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "generated_at_utc": str(payload.get("generated_at_utc") or ""),
            "source_version": str(payload.get("source_version") or ""),
            "row_counts": {str(k): len(v) for k, v in safe["sections"].items()},
            "total_rows": total_rows,
        }
        meta = {
            "active_snapshot_id": snapshot_id,
            "imported_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "manifest_json": _canonical(manifest),
            "collection_status_json": _canonical(safe.get("collection_status") or {}),
            "readiness_json": _canonical(safe.get("readiness") or {}),
            "source_counts_json": _canonical(safe.get("source_counts") or {}),
        }
        for key, value in meta.items():
            conn.execute(
                "INSERT INTO serving_meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        conn.execute("DELETE FROM serving_rows WHERE snapshot_id<>?", (snapshot_id,))
    return manifest


def query_rows(
    section,
    *,
    query="",
    categories=None,
    fiscal_year=None,
    start_date="",
    end_date="",
    limit=200,
    offset=0,
):
    snapshot_id = active_snapshot_id()
    if not snapshot_id:
        return []
    params = [snapshot_id, str(section)]
    where = ["snapshot_id=?", "section=?"]
    selected = [str(x).upper() for x in (categories or ()) if str(x).strip()]
    if categories is not None:
        if not selected:
            return []
        where.append("category IN (%s)" % ",".join("?" for _ in selected))
        params.extend(selected)
    if fiscal_year is not None:
        where.append("fiscal_year=?")
        params.append(str(fiscal_year))

    # Shopping source_date is stored in sort_text. Apply the date window inside
    # SQLite so RESULT_SERVER never materializes thousands of rows only to
    # discard most of them in Python.
    if str(section) == "shopping":
        start_text = str(start_date or "").strip()
        end_text = str(end_date or "").strip()
        if start_text:
            where.append("sort_text>=?")
            params.append(start_text)
        if end_text:
            where.append("sort_text<=?")
            params.append(end_text)
    q = str(query or "").casefold().strip()
    if q:
        esc_char = chr(92)
        escaped = q.replace(esc_char, esc_char + esc_char).replace("%", esc_char + "%").replace("_", esc_char + "_")
        where.append("search_text LIKE ? ESCAPE '\\'")
        params.append("%" + escaped + "%")
    size = max(1, min(int(limit), 5000))
    start = max(0, int(offset))
    params.extend([size, start])
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT payload_json FROM serving_rows
                WHERE {' AND '.join(where)}
                ORDER BY sort_num DESC,sort_text DESC,row_key
                LIMIT ? OFFSET ?""",
            tuple(params),
        ).fetchall()
    result = []
    for row in rows:
        try:
            value = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def _all_pages(fetch, *, page_size=5000):
    rows = []
    offset = 0
    while True:
        batch = fetch(limit=page_size, offset=offset)
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += len(batch)
        if len(rows) > MAX_SNAPSHOT_ROWS:
            raise RuntimeError("local result snapshot row limit exceeded")
    return rows


def build_local_snapshot():
    """Build a compact local snapshot from normalized shopping and local analysis data."""
    import budget_read_vnext
    import budget_storage
    import collection_monitor_vnext
    import procurement_read_vnext
    import readiness_vnext
    import runtime_role
    from app_version import APP_VERSION
    from db import connect
    from vnext_schema import CLASSIFIER_VERSION

    shopping = procurement_read_vnext.shopping_rows(limit=None)
    vendors = procurement_read_vnext.vendor_rows(limit=None)
    budget_targets = _all_pages(
        lambda limit, offset: budget_read_vnext.target_budget_rows(limit=limit, offset=offset)
    )
    budget_prebid = _all_pages(
        lambda limit, offset: budget_read_vnext.prebid_budget_rows(limit=limit, offset=offset)
    )

    raw_counts = {}
    target_counts = {}
    history_counts = {}
    inactive_counts = {}
    with connect() as conn:
        for row in conn.execute(
            "SELECT dataset,COUNT(*) n FROM raw_records GROUP BY dataset"
        ).fetchall():
            raw_counts[str(row["dataset"])] = int(row["n"] or 0)
        for row in conn.execute(
            """SELECT r.dataset,COUNT(*) n
               FROM raw_records r JOIN classifications c
                 ON c.entity_type=r.dataset AND c.entity_key=r.source_key
                AND c.classifier_version=?
                AND c.source_payload_sha256=r.payload_sha256
               WHERE c.primary_category IN ('LIGHTING','POLE','ELECTRICAL','SOLAR')
               GROUP BY r.dataset""",
            (CLASSIFIER_VERSION,),
        ).fetchall():
            target_counts[str(row["dataset"])] = int(row["n"] or 0)

    # Shopping snapshots represent the active normalized business view. Preserve
    # history/inactive counts separately so RESULT_SERVER dashboards never treat
    # reconciled stale rows as current opportunities.
    test_mode = str(os.getenv("G2B_TEST_MODE", "0") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    use_legacy_test_raw = bool(test_mode and not runtime_role.is_local_collector())
    if use_legacy_test_raw:
        current_shopping = len(shopping)
        raw_counts["shopping_delivery"] = current_shopping
        target_counts["shopping_delivery"] = current_shopping
        history_counts["shopping_delivery"] = current_shopping
        inactive_counts["shopping_delivery"] = 0
    else:
        import shopping_store_v41
        shopping_counts = shopping_store_v41.count()
        raw_counts["shopping_delivery"] = int(
            shopping_counts.get("active_records") or 0
        )
        target_counts["shopping_delivery"] = int(
            shopping_counts.get("active_records") or 0
        )
        history_counts["shopping_delivery"] = int(
            shopping_counts.get("history_records") or 0
        )
        inactive_counts["shopping_delivery"] = int(
            shopping_counts.get("inactive_records") or 0
        )

    # In 4.x production, budget current RAW is authoritative in PostgreSQL. Never
    # let compatibility snapshot metadata fall back to old SQLite budget remnants.
    if budget_storage.using_postgres():
        budget_datasets = tuple(sorted(budget_storage.BUDGET_DATASETS))
        current_hashes = budget_storage.current_payload_hashes(budget_datasets)
        for dataset in budget_datasets:
            raw_counts[dataset] = sum(
                1 for current_dataset, _key in current_hashes
                if current_dataset == dataset
            )
            history_counts[dataset] = raw_counts[dataset]
            inactive_counts[dataset] = 0
            target_counts[dataset] = 0

        placeholders = ",".join("?" for _ in budget_datasets)
        with connect() as conn:
            rows = conn.execute(
                f"""SELECT entity_type,entity_key,primary_category,source_payload_sha256
                    FROM classifications
                    WHERE classifier_version=?
                      AND entity_type IN ({placeholders})""",
                (CLASSIFIER_VERSION, *budget_datasets),
            ).fetchall()
        target_categories = {"LIGHTING", "POLE", "ELECTRICAL", "SOLAR"}
        for row in rows:
            key = (str(row["entity_type"]), str(row["entity_key"]))
            if (
                current_hashes.get(key, "")
                == str(row["source_payload_sha256"] or "")
                and str(row["primary_category"] or "").upper()
                in target_categories
            ):
                target_counts[key[0]] = target_counts.get(key[0], 0) + 1

    for dataset, value in raw_counts.items():
        history_counts.setdefault(dataset, int(value or 0))
        inactive_counts.setdefault(dataset, 0)

    generated = dt.datetime.now(dt.timezone.utc).isoformat()
    sections = {
        "shopping": shopping,
        "vendors": vendors,
        "budget_targets": budget_targets,
        "budget_prebid": budget_prebid,
    }
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "generated_at_utc": generated,
        "source_version": APP_VERSION,
        "sections": _json_safe(sections),
        "collection_status": _json_safe(collection_monitor_vnext.monitor_snapshot()),
        "readiness": _json_safe(readiness_vnext.build_readiness_report()),
        "source_counts": {
            # Compatibility key "raw" now means current active normalized rows
            # for shopping; preserved history is reported explicitly.
            "raw": raw_counts,
            "target": target_counts,
            "history": history_counts,
            "inactive": inactive_counts,
        },
    }
    payload["snapshot_id"] = _hash({
        "generated_at_utc": generated,
        "source_version": APP_VERSION,
        "row_counts": {key: len(value) for key, value in sections.items()},
    })
    return payload
