"""Read-only SEOA bridge for AI-SPECE / Cafe24 AI SPACE.

Every database access in this module uses SQLite URI mode=ro plus
PRAGMA query_only=ON. It never calls source APIs, schema installers,
collectors, normalizers, classifiers, schedulers, or settings writers.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request

import db
from app_version import APP_VERSION
from vnext_schema import CLASSIFIER_VERSION


BRIDGE_PATH = "/api/seoa-bridge/v1/read"
BRIDGE_VERSION = "1"
BRIDGE_SECRET_ENV = "AI_SPACE_SEOA_BRIDGE_SECRET"
MAX_BODY_BYTES = 8_192
MAX_CLOCK_SKEW_SECONDS = 90
MAX_NONCES = 2_048
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_DATASETS = (
    "budget",
    "shopping_delivery",
    "bid_notice_goods",
    "bid_notice_service",
    "opening_result_service",
    "award_result_service",
    "contract_service",
)
PROCUREMENT_DATASETS = {
    "goods": ("bid_notice_goods", "shopping_delivery"),
    "services": (
        "bid_notice_service",
        "opening_result_service",
        "award_result_service",
        "contract_service",
    ),
}
TREND_DATASET_METRICS = {
    "goods": {
        "bid_notice_goods": "opportunity_count",
        "shopping_delivery": "delivery_count",
        "budget": "budget_count",
    },
    "services": {
        "bid_notice_service": "opportunity_count",
        "award_result_service": "award_count",
        "contract_service": "contract_count",
        "budget": "budget_count",
    },
}
SAFE_CATEGORY_RE = re.compile(r"^[A-Za-z0-9가-힣 _.-]{1,80}$")
REQUIRED_COLUMNS = {
    "raw_records": {"dataset", "source_key", "source_date", "payload_sha256"},
    "classifications": {
        "entity_type",
        "entity_key",
        "primary_category",
        "classifier_version",
        "source_payload_sha256",
    },
    "collection_checkpoints": {"dataset", "status"},
}

_nonce_lock = threading.Lock()
_recent_nonces: OrderedDict[str, int] = OrderedDict()


def _bridge_secret() -> str:
    value = str(os.getenv(BRIDGE_SECRET_ENV, "") or "")
    if not value:
        return ""
    if (
        value != value.strip()
        or any(ch.isspace() for ch in value)
        or len(value) < 32
    ):
        return ""
    return value


def _canonical_message(
    method: str,
    path: str,
    timestamp: int,
    nonce: str,
    body: bytes,
) -> bytes:
    return (
        "SEOA-BRIDGE-V1\n"
        + method.upper() + "\n"
        + path + "\n"
        + str(timestamp) + "\n"
        + nonce + "\n"
        + hashlib.sha256(body).hexdigest()
    ).encode("utf-8")


def _claim_nonce(nonce: str, timestamp: int, now: int) -> bool:
    with _nonce_lock:
        cutoff = now - MAX_CLOCK_SKEW_SECONDS
        for key, seen_at in list(_recent_nonces.items()):
            if seen_at < cutoff:
                _recent_nonces.pop(key, None)
        if nonce in _recent_nonces:
            return False
        _recent_nonces[nonce] = timestamp
        while len(_recent_nonces) > MAX_NONCES:
            _recent_nonces.popitem(last=False)
        return True


def verify_signed_request(
    secret: str,
    *,
    method: str,
    path: str,
    body: bytes,
    headers,
    now: int | None = None,
) -> bool:
    try:
        if (
            not secret
            or secret != secret.strip()
            or any(ch.isspace() for ch in secret)
            or len(secret) < 32
        ):
            return False
        if method.upper() != "POST" or path != BRIDGE_PATH:
            return False
        if headers.get("x-seoa-bridge-version") != BRIDGE_VERSION:
            return False
        timestamp = int(headers["x-seoa-bridge-timestamp"])
        nonce = str(headers["x-seoa-bridge-nonce"]).lower()
        signature = str(headers["x-seoa-bridge-signature"]).lower()
        if not NONCE_RE.fullmatch(nonce) or not SIGNATURE_RE.fullmatch(signature):
            return False
        current = int(time.time()) if now is None else int(now)
        if abs(current - timestamp) > MAX_CLOCK_SKEW_SECONDS:
            return False
        expected = hmac.new(
            secret.encode("utf-8"),
            _canonical_message(method, path, timestamp, nonce, body),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return False
        return _claim_nonce(nonce, timestamp, current)
    except (KeyError, TypeError, ValueError):
        return False


def _strict_payload(body: bytes) -> tuple[str, dict]:
    try:
        payload = json.loads(
            body.decode("utf-8"),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
    except (UnicodeError, ValueError, RecursionError):
        raise HTTPException(422, "SEOA_BRIDGE_INVALID_BODY") from None
    if not isinstance(payload, dict) or set(payload) != {"operation", "parameters"}:
        raise HTTPException(422, "SEOA_BRIDGE_INVALID_BODY")
    operation = payload.get("operation")
    parameters = payload.get("parameters")
    if not isinstance(operation, str) or not isinstance(parameters, dict):
        raise HTTPException(422, "SEOA_BRIDGE_INVALID_BODY")
    operation = operation.strip().lower()
    if operation not in {
        "health.read",
        "readiness.read",
        "procurement_context.read",
        "procurement_trend.read",
    }:
        raise HTTPException(403, "SEOA_BRIDGE_OPERATION_DENIED")
    if len(parameters) > 20:
        raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID")
    return operation, parameters


def _readonly_uri(path: str) -> str:
    absolute = str(Path(path).expanduser().resolve())
    return "file:" + quote(absolute, safe="/:") + "?mode=ro"


@contextmanager
def readonly_connection(path: str | None = None):
    target = str(path or db.DB_PATH or "").strip()
    if not target or not Path(target).expanduser().is_file():
        raise HTTPException(503, "SEOA_BRIDGE_DB_NOT_READY")
    try:
        conn = sqlite3.connect(
            _readonly_uri(target),
            uri=True,
            timeout=5,
        )
    except sqlite3.Error:
        raise HTTPException(503, "SEOA_BRIDGE_DB_NOT_READY") from None
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        yield conn
    finally:
        conn.close()


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(" + table + ")").fetchall()
    }


def _schema_gaps(conn) -> dict[str, list[str]]:
    gaps = {}
    for table, required in REQUIRED_COLUMNS.items():
        missing = sorted(required - _table_columns(conn, table))
        if missing:
            gaps[table] = missing
    return gaps


def _setting_present(conn, key: str) -> bool:
    if not _table_exists(conn, "app_settings"):
        return False
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key=?",
        (key,),
    ).fetchone()
    return bool(row and str(row["value"] or "").strip())


def _credential_flags(conn) -> dict:
    return {
        "g2b_service_key_configured": bool(
            str(os.getenv("G2B_SERVICE_KEY", "") or "").strip()
            or _setting_present(conn, "api_key")
        ),
        "lofin_api_key_configured": bool(
            str(os.getenv("LOFIN_API_KEY", "") or "").strip()
            or _setting_present(conn, "lofin_api_key")
        ),
    }


def _safe_health_projection(health: dict) -> dict:
    sync = health.get("sync") if isinstance(health.get("sync"), dict) else {}
    return {
        "status": str(health.get("status") or "unknown")[:80],
        "configured": bool(health.get("configured")),
        "backend_ok": bool(health.get("backend_ok")),
        "version": str(health.get("version") or APP_VERSION)[:80],
        "test_mode": bool(health.get("test_mode")),
        "sample_generation": bool(health.get("sample_generation")),
        "sync_status": str(sync.get("status") or "")[:80] or None,
        "sync_job": str(sync.get("job") or "")[:120] or None,
    }


def _readiness_projection(conn) -> dict:
    required = {
        "raw_records",
        "classifications",
        "collection_checkpoints",
    }
    existing = {
        name for name in required
        if _table_exists(conn, name)
    }
    credentials = _credential_flags(conn)
    missing = sorted(required - existing)
    schema_gaps = _schema_gaps(conn)

    raw_counts = {}
    checkpoint_counts = {}
    if "raw_records" in existing:
        for dataset in EXPECTED_DATASETS:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM raw_records WHERE dataset=?",
                (dataset,),
            ).fetchone()
            raw_counts[dataset] = int(row["n"] or 0)
    if "collection_checkpoints" in existing:
        allowed_statuses = {"IDLE", "RUNNING", "COMPLETE", "INCOMPLETE", "FAILED"}
        for dataset in EXPECTED_DATASETS:
            rows = conn.execute(
                "SELECT status,COUNT(*) AS n FROM collection_checkpoints "
                "WHERE dataset=? GROUP BY status ORDER BY status",
                (dataset,),
            ).fetchall()
            grouped: dict[str, int] = {}
            for row in rows:
                status = str(row["status"] or "OTHER")
                safe_status = status if status in allowed_statuses else "OTHER"
                grouped[safe_status] = grouped.get(safe_status, 0) + int(row["n"] or 0)
            checkpoint_counts[dataset] = grouped

    if missing or schema_gaps:
        status = "FOUNDATION_NOT_READY"
    elif not credentials["g2b_service_key_configured"]:
        status = "G2B_KEY_MISSING"
    else:
        # This bridge deliberately does not execute or attest a canary. A key
        # plus existing tables means the read-only projection is usable, not
        # that live-source validation has passed.
        status = "READ_ONLY_READY"

    return {
        "status": status,
        "classifier_version": CLASSIFIER_VERSION,
        "required_tables_present": not missing,
        "missing_tables": missing,
        "schema_compatible": not schema_gaps,
        "missing_columns": schema_gaps,
        "credentials": credentials,
        "raw_counts": raw_counts,
        "checkpoint_status_counts": checkpoint_counts,
        "historical_live_collection_locked_by_default": True,
        "main_merge_hold": True,
        "external_api_calls": 0,
        "writes_performed": 0,
    }


def _context_parameters(parameters: dict) -> tuple[str, int, int]:
    if set(parameters) - {"kind", "days", "limit"}:
        raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID")
    kind = str(parameters.get("kind", "goods") or "").strip().lower()
    if kind not in PROCUREMENT_DATASETS:
        raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID")
    try:
        days = int(parameters.get("days", 365))
        limit = int(parameters.get("limit", 20))
    except (TypeError, ValueError):
        raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID") from None
    if not 1 <= days <= 3660 or not 1 <= limit <= 100:
        raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID")
    return kind, days, limit


def _procurement_context(conn, parameters: dict) -> dict:
    kind, days, limit = _context_parameters(parameters)
    gaps = _schema_gaps(conn)
    for table in ("raw_records", "classifications"):
        if not _table_exists(conn, table) or table in gaps:
            raise HTTPException(503, "SEOA_BRIDGE_FOUNDATION_NOT_READY")

    today = dt.datetime.now(ZoneInfo("Asia/Seoul")).date()
    since = (today - dt.timedelta(days=days - 1)).isoformat()
    datasets = PROCUREMENT_DATASETS[kind]
    output = {}
    total_raw = 0
    total_current = 0

    for dataset in datasets:
        raw = int(conn.execute(
            "SELECT COUNT(*) AS n FROM raw_records "
            "WHERE dataset=? AND source_date>=?",
            (dataset, since),
        ).fetchone()["n"] or 0)
        current = int(conn.execute(
            "SELECT COUNT(*) AS n "
            "FROM raw_records r JOIN classifications c "
            "ON c.entity_type=r.dataset AND c.entity_key=r.source_key "
            "AND c.classifier_version=? "
            "AND COALESCE(c.source_payload_sha256,'')=COALESCE(r.payload_sha256,'') "
            "WHERE r.dataset=? AND r.source_date>=?",
            (CLASSIFIER_VERSION, dataset, since),
        ).fetchone()["n"] or 0)
        category_rows = conn.execute(
            "SELECT c.primary_category,COUNT(*) AS n "
            "FROM raw_records r JOIN classifications c "
            "ON c.entity_type=r.dataset AND c.entity_key=r.source_key "
            "AND c.classifier_version=? "
            "AND COALESCE(c.source_payload_sha256,'')=COALESCE(r.payload_sha256,'') "
            "WHERE r.dataset=? AND r.source_date>=? "
            "GROUP BY c.primary_category "
            "ORDER BY n DESC,c.primary_category ASC LIMIT ?",
            (CLASSIFIER_VERSION, dataset, since, limit),
        ).fetchall()
        categories = {
            str(row["primary_category"] or "UNCLASSIFIED")[:80]: int(row["n"] or 0)
            for row in category_rows
        }
        output[dataset] = {
            "raw_count": raw,
            "current_classified_count": current,
            "unclassified_or_stale_count": max(0, raw - current),
            "category_counts": categories,
        }
        total_raw += raw
        total_current += current

    return {
        "kind": kind,
        "days": days,
        "since_kst": since,
        "datasets": output,
        "raw_total": total_raw,
        "current_classified_total": total_current,
        "unclassified_or_stale_total": max(0, total_raw - total_current),
        "classifier_version": CLASSIFIER_VERSION,
        "external_api_calls": 0,
        "writes_performed": 0,
        "raw_payloads_returned": False,
        "vendor_identity_returned": False,
    }


def _month_key(value: str) -> tuple[int, int]:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", text):
        raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID")
    year, month = map(int, text.split("-"))
    if year < 2000 or year > 2100 or month < 1 or month > 12:
        raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID")
    return year, month


def _trend_parameters(parameters: dict) -> tuple[str, int, int, str]:
    if set(parameters) - {"kind", "months", "limit", "end_month"}:
        raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID")
    kind = str(parameters.get("kind", "goods") or "").strip().lower()
    if kind not in TREND_DATASET_METRICS:
        raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID")
    try:
        months = int(parameters.get("months", 6))
        limit = int(parameters.get("limit", 10))
    except (TypeError, ValueError):
        raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID") from None
    if not 3 <= months <= 24 or not 1 <= limit <= 20:
        raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID")
    end_month = str(parameters.get("end_month") or "").strip()
    if not end_month:
        end_month = dt.datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m")
    _month_key(end_month)
    return kind, months, limit, end_month


def _month_sequence(end_month: str, count: int) -> list[str]:
    year, month = _month_key(end_month)
    values = []
    cursor = year * 12 + (month - 1)
    for offset in range(count - 1, -1, -1):
        value = cursor - offset
        y, zero_month = divmod(value, 12)
        values.append(f"{y:04d}-{zero_month + 1:02d}")
    return values


def _procurement_trend(conn, parameters: dict) -> dict:
    kind, months_count, limit, end_month = _trend_parameters(parameters)
    gaps = _schema_gaps(conn)
    for table in ("raw_records", "classifications"):
        if not _table_exists(conn, table) or table in gaps:
            raise HTTPException(503, "SEOA_BRIDGE_FOUNDATION_NOT_READY")

    months = _month_sequence(end_month, months_count)
    first_month = months[0]
    metric_map = TREND_DATASET_METRICS[kind]
    datasets = tuple(metric_map)
    placeholders = ",".join("?" for _ in datasets)
    rows = conn.execute(
        f"""
        SELECT r.dataset,
               substr(r.source_date,1,7) AS period,
               c.primary_category,
               COUNT(*) AS n
        FROM raw_records r
        JOIN classifications c
          ON c.entity_type=r.dataset
         AND c.entity_key=r.source_key
         AND c.classifier_version=?
         AND COALESCE(c.source_payload_sha256,'')=COALESCE(r.payload_sha256,'')
        WHERE r.dataset IN ({placeholders})
          AND length(r.source_date)>=7
          AND substr(r.source_date,1,7)>=?
          AND substr(r.source_date,1,7)<=?
        GROUP BY r.dataset,period,c.primary_category
        ORDER BY period,r.dataset,c.primary_category
        """,
        (CLASSIFIER_VERSION, *datasets, first_month, end_month),
    ).fetchall()

    category_totals: dict[str, int] = {}
    counts: dict[tuple[str, str, str], int] = {}
    for row in rows:
        period = str(row["period"] or "")
        if period not in months:
            continue
        category = str(row["primary_category"] or "UNCLASSIFIED").strip()
        if not SAFE_CATEGORY_RE.fullmatch(category):
            continue
        dataset = str(row["dataset"])
        value = int(row["n"] or 0)
        category_totals[category] = category_totals.get(category, 0) + value
        counts[(category, period, dataset)] = value

    selected = [
        category
        for category, _ in sorted(
            category_totals.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    ]
    metric_names = tuple(dict.fromkeys(metric_map.values()))
    segments = []
    for category in selected:
        observations = []
        for period in months:
            observation = {
                "period": period,
                **{name: 0 for name in metric_names},
            }
            source_count = 0
            for dataset, metric in metric_map.items():
                value = counts.get((category, period, dataset), 0)
                observation[metric] += value
                source_count += value
            observation["source_count"] = source_count
            observations.append(observation)
        segments.append({
            "segment": category,
            "observations": observations,
        })

    return {
        "kind": kind,
        "months": months,
        "classifier_version": CLASSIFIER_VERSION,
        "segments": segments,
        "segment_limit": limit,
        "external_api_calls": 0,
        "writes_performed": 0,
        "raw_payloads_returned": False,
        "vendor_identity_returned": False,
    }


def register_seoa_bridge_routes(
    app,
    *,
    health_reader: Callable[[], dict],
) -> None:
    @app.post(BRIDGE_PATH, include_in_schema=False)
    async def seoa_bridge_read(request: Request):
        secret = _bridge_secret()
        if not secret:
            raise HTTPException(503, "SEOA_BRIDGE_NOT_CONFIGURED")

        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "SEOA_BRIDGE_BODY_TOO_LARGE")
        headers = {key.lower(): value for key, value in request.headers.items()}
        if not verify_signed_request(
            secret,
            method=request.method,
            path=request.url.path,
            body=body,
            headers=headers,
        ):
            raise HTTPException(401, "SEOA_BRIDGE_AUTH_FAILED")

        operation, parameters = _strict_payload(body)
        if operation == "health.read":
            if parameters:
                raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID")
            health = health_reader()
            if not isinstance(health, dict):
                raise HTTPException(503, "SEOA_BRIDGE_NOT_READY")
            status, data = "OK", _safe_health_projection(health)
        elif operation == "readiness.read":
            if parameters:
                raise HTTPException(422, "SEOA_BRIDGE_PARAMETERS_INVALID")
            with readonly_connection() as conn:
                data = _readiness_projection(conn)
            status = "OK" if data["status"] == "READ_ONLY_READY" else "NOT_READY"
        elif operation == "procurement_context.read":
            with readonly_connection() as conn:
                data = _procurement_context(conn, parameters)
            status = "OK"
        elif operation == "procurement_trend.read":
            with readonly_connection() as conn:
                data = _procurement_trend(conn, parameters)
            status = "OK"
        else:  # pragma: no cover
            raise HTTPException(403, "SEOA_BRIDGE_OPERATION_DENIED")

        return {
            "status": status,
            "source_version": APP_VERSION,
            "source_build": None,
            "data": data,
        }


__all__ = [
    "BRIDGE_PATH",
    "BRIDGE_SECRET_ENV",
    "_procurement_context",
    "_procurement_trend",
    "_readiness_projection",
    "_safe_health_projection",
    "readonly_connection",
    "register_seoa_bridge_routes",
    "verify_signed_request",
]
