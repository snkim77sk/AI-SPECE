"""Read-only collection monitor for G2B 4.x.

The product has four source stages only:
- shopping delivery requests (stored only for lighting/poles from 2026-09-01),
- QWGJK normalized budget projects,
- AIDFA normalized appropriation facts,
- education budget records (transport remains HOLD until validated).
"""
from __future__ import annotations

import datetime as dt
import math
import os
from collections import Counter

import budget_collection_status_vnext
from db import connect
from vnext_store import ensure_foundation
import shopping_store_v41

RUNNING_STALE_SECONDS = 5 * 60

STAGES = (
    {
        "dataset": "shopping_delivery",
        "number": "01",
        "label": "조명·등주 쇼핑몰 납품요구",
        "group": "나라장터",
        "live_gate": "OPERATIONAL · FORWARD_FROM_2026-09-01 · NORMALIZED_TARGET_ONLY",
    },
    {
        "dataset": "budget",
        "number": "02",
        "label": "지방재정365 세부사업·집행",
        "group": "예산",
        "live_gate": "OPERATIONAL_BUDGET · HISTORY_FROM_2026-01-01 · NORMALIZED_POSTGRESQL",
    },
    {
        "dataset": "budget_appropriation",
        "number": "03",
        "label": "지방재정365 세출예산(AIDFA)",
        "group": "미래예산",
        "live_gate": "OPERATIONAL_BUDGET · NEXT_YEAR_DAILY_REFRESH",
    },
    {
        "dataset": "education_budget",
        "number": "04",
        "label": "교육청 예산",
        "group": "예산",
        "live_gate": "HOLD · TRANSPORT_VALIDATION_REQUIRED",
    },
)

STATUS_LABELS = {
    "RUNNING": "실행중",
    "COMPLETE": "완료",
    "FAILED": "오류",
    "INCOMPLETE": "중단",
    "STALE": "갱신중단",
    "IDLE": "대기",
    "DATA_ONLY": "자료있음",
    "NOT_STARTED": "미수집",
}


def _utc_now():
    return dt.datetime.now(dt.timezone.utc)


def _parse_utc(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _progress(row):
    if not row:
        return {
            "pages_processed": 0, "total_pages": None, "percent": None,
            "fetched_count": 0, "saved_count": 0, "source_total": None,
        }
    page_no = max(0, int(row.get("page_no") or 0))
    page_size = max(0, int(row.get("page_size") or 0))
    fetched = max(0, int(row.get("fetched_count") or 0))
    saved = max(0, int(row.get("saved_count") or 0))
    raw_total = int(row.get("source_total") or 0)
    source_total = raw_total if raw_total > 0 else None
    total_pages = (
        int(math.ceil(source_total / page_size))
        if source_total is not None and page_size > 0 else None
    )
    percent = (
        min(100.0, round((fetched / source_total) * 100.0, 1))
        if source_total else None
    )
    return {
        "pages_processed": max(0, page_no - 1),
        "total_pages": total_pages,
        "percent": percent,
        "fetched_count": fetched,
        "saved_count": saved,
        "source_total": source_total,
    }


def _state_for(latest, raw_count, now):
    if latest:
        status = str(latest.get("status") or "IDLE")
        if status == "RUNNING":
            stamp = _parse_utc(latest.get("updated_at"))
            if stamp is not None and (now - stamp).total_seconds() > RUNNING_STALE_SECONDS:
                return "STALE"
        if status in STATUS_LABELS:
            return status
    return "DATA_ONLY" if raw_count else "NOT_STARTED"


def _stage_message(state, latest, progress, raw_count):
    scope = str((latest or {}).get("scope_key") or "")
    prefix = f"{scope} · " if scope else ""
    if state == "RUNNING":
        return f"{prefix}{progress['pages_processed']}페이지 · {progress['saved_count']:,}건 저장"
    if state == "COMPLETE":
        return f"{prefix}완료 · {progress['saved_count']:,}건 저장"
    if state in {"FAILED", "INCOMPLETE"}:
        return f"{prefix}{str((latest or {}).get('last_error') or '오류')}"
    if state == "STALE":
        return f"{prefix}5분 이상 갱신되지 않았습니다"
    if state == "DATA_ONLY":
        return f"현재 저장 {raw_count:,}건"
    return "아직 수집 실행 이력이 없습니다"


def _shopping_stage(conn, spec, now):
    rows = [
        dict(row) for row in conn.execute(
            """SELECT dataset,scope_key,range_start,range_end,page_no,page_size,
                      source_total,fetched_count,saved_count,status,last_error,updated_at
               FROM collection_checkpoints WHERE dataset=?
               ORDER BY updated_at DESC,scope_key DESC""",
            (spec["dataset"],),
        ).fetchall()
    ]
    latest = rows[0] if rows else None
    if str(os.getenv("G2B_TEST_MODE", "0") or "").lower() in {"1", "true", "yes", "on"}:
        raw_row = conn.execute(
            "SELECT COUNT(*) n,MAX(fetched_at) last_at FROM raw_records WHERE dataset=?",
            (spec["dataset"],),
        ).fetchone()
    else:
        raw_row = conn.execute(
            "SELECT COUNT(*) n,MAX(updated_at) last_at FROM shopping_records"
        ).fetchone()
    raw_count = int(raw_row["n"] or 0) if raw_row else 0
    state = _state_for(latest, raw_count, now)
    progress = _progress(latest)
    counts = Counter(str(row.get("status") or "IDLE") for row in rows)
    return {
        **spec,
        "state": state,
        "state_label": STATUS_LABELS.get(state, state),
        "scope": str((latest or {}).get("scope_key") or ""),
        "range_start": str((latest or {}).get("range_start") or ""),
        "range_end": str((latest or {}).get("range_end") or ""),
        "last_activity": str((latest or {}).get("updated_at") or (raw_row["last_at"] if raw_row else "") or ""),
        "last_error": str((latest or {}).get("last_error") or ""),
        "raw_count": raw_count,
        "checkpoint_count": len(rows),
        "complete_scopes": int(counts.get("COMPLETE", 0)),
        "running_scopes": int(counts.get("RUNNING", 0)),
        "failed_scopes": int(counts.get("FAILED", 0)),
        "incomplete_scopes": int(counts.get("INCOMPLETE", 0)),
        "message": _stage_message(state, latest, progress, raw_count),
        **progress,
    }, rows


BUDGET_HISTORY_START_DATE = dt.date(2026, 1, 1)
_KST = dt.timezone(dt.timedelta(hours=9))


def _budget_scope_day(scope_key):
    parts = str(scope_key or "").split(":")
    try:
        if len(parts) == 2:
            year = int(parts[0])
            day = dt.date.fromisoformat(parts[1])
        elif len(parts) == 3 and parts[0] == "history":
            year = int(parts[1])
            day = dt.date.fromisoformat(parts[2])
        else:
            return None
    except (TypeError, ValueError):
        return None
    return day if day.year == year else None


def _budget_history_progress(scopes, now):
    stamp = now or _utc_now()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    latest = stamp.astimezone(_KST).date() - dt.timedelta(days=1)
    start = BUDGET_HISTORY_START_DATE
    if latest < start:
        return {
            "history_start_date": start.isoformat(),
            "history_latest_date": latest.isoformat(),
            "history_complete_days": 0,
            "history_total_days": 0,
            "history_percent": 100.0,
            "history_next_date": "",
        }

    complete_days = set()
    for row in scopes or ():
        if str(row.get("status") or "").upper() != "COMPLETE":
            continue
        day = _budget_scope_day(row.get("scope_key"))
        if day is not None and start <= day <= latest:
            complete_days.add(day)

    total_days = (latest - start).days + 1
    next_day = start
    while next_day <= latest and next_day in complete_days:
        next_day += dt.timedelta(days=1)
    return {
        "history_start_date": start.isoformat(),
        "history_latest_date": latest.isoformat(),
        "history_complete_days": len(complete_days),
        "history_total_days": total_days,
        "history_percent": round(
            min(100.0, (len(complete_days) / total_days) * 100.0),
            1,
        ),
        "history_next_date": (
            "" if next_day > latest else next_day.isoformat()
        ),
    }


def _budget_stage(spec, dataset_status, now):
    scopes = list(dataset_status.get("scopes") or [])
    scopes.sort(key=lambda row: (str(row.get("updated_at") or ""), str(row.get("scope_key") or "")), reverse=True)
    latest = scopes[0] if scopes else None
    raw_count = int(dataset_status.get("raw_rows") or 0)
    state = _state_for(latest, raw_count, now)
    progress = _progress(latest)
    history_progress = (
        _budget_history_progress(scopes, now)
        if str(spec.get("dataset") or "") == "budget"
        else {}
    )
    return {
        **spec,
        **history_progress,
        "state": state,
        "state_label": STATUS_LABELS.get(state, state),
        "scope": str((latest or {}).get("scope_key") or ""),
        "range_start": str((latest or {}).get("range_start") or ""),
        "range_end": str((latest or {}).get("range_end") or ""),
        "last_activity": str((latest or {}).get("updated_at") or ""),
        "last_error": str((latest or {}).get("last_error") or ""),
        "raw_count": raw_count,
        "raw_revisions": int(dataset_status.get("raw_revisions") or 0),
        "raw_backend": str(dataset_status.get("raw_backend") or ""),
        "checkpoint_count": int(dataset_status.get("checkpoint_count") or 0),
        "complete_scopes": int(dataset_status.get("verified_complete_scopes") or 0),
        "running_scopes": int((dataset_status.get("checkpoint_status_counts") or {}).get("RUNNING", 0)),
        "failed_scopes": int((dataset_status.get("checkpoint_status_counts") or {}).get("FAILED", 0)),
        "incomplete_scopes": int((dataset_status.get("checkpoint_status_counts") or {}).get("INCOMPLETE", 0)),
        "message": _stage_message(state, latest, progress, raw_count),
        **progress,
    }, scopes


def monitor_snapshot(*, recent_limit=30, now=None):
    ensure_foundation()
    shopping_store_v41.ensure_schema()
    current = now or _utc_now()

    with connect() as conn:
        shopping, shopping_rows = _shopping_stage(conn, STAGES[0], current)

    try:
        budget_status = budget_collection_status_vnext.budget_collection_status()
        budget_by_name = {
            str(row["dataset"]): row for row in budget_status.get("datasets") or []
        }
    except Exception as exc:
        budget_by_name = {}
        budget_status = {
            "error": type(exc).__name__,
            "source_collection_completeness_verified": False,
        }

    stages = [shopping]
    recent = []
    for row in shopping_rows:
        recent.append({**_progress(row), **{
            "dataset": "shopping_delivery",
            "label": STAGES[0]["label"],
            "scope": str(row.get("scope_key") or ""),
            "status": str(row.get("status") or "IDLE"),
            "status_label": STATUS_LABELS.get(str(row.get("status") or "IDLE"), str(row.get("status") or "IDLE")),
            "last_error": str(row.get("last_error") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }})

    for spec in STAGES[1:]:
        dataset_status = budget_by_name.get(spec["dataset"], {
            "dataset": spec["dataset"], "raw_rows": 0, "raw_revisions": 0,
            "checkpoint_count": 0, "checkpoint_status_counts": {}, "scopes": [],
        })
        stage, scopes = _budget_stage(spec, dataset_status, current)
        stages.append(stage)
        for row in scopes:
            recent.append({**_progress(row), **{
                "dataset": spec["dataset"],
                "label": spec["label"],
                "scope": str(row.get("scope_key") or ""),
                "status": str(row.get("status") or "IDLE"),
                "status_label": STATUS_LABELS.get(str(row.get("status") or "IDLE"), str(row.get("status") or "IDLE")),
                "last_error": str(row.get("last_error") or ""),
                "updated_at": str(row.get("updated_at") or ""),
            }})

    recent.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    recent = recent[:max(1, min(int(recent_limit), 100))]
    states = Counter(stage["state"] for stage in stages)
    last_activity = max(
        (str(stage["last_activity"]) for stage in stages if stage["last_activity"]),
        default="",
    )
    return {
        "generated_at_utc": current.isoformat(),
        "refresh_hint_seconds": 5,
        "monitor_scope": "G2B_V4_BUDGET_AND_TARGET_SHOPPING",
        "source_io_performed": False,
        "collection_controls_enabled": True,
        "operational_recent": {
            "dataset": "shopping_delivery",
            "order": "FORWARD",
            "start_date": "2026-09-01",
            "one_day_scopes": True,
            "latest_boundary": "D-1",
            "stored_scope": "LIGHTING_AND_POLE_ONLY",
        },
        "budget_storage": budget_status,
        "safety": {
            "service_collection_removed": True,
            "goods_bid_collection_removed": True,
            "education_live_transport_hold": True,
        },
        "summary": {
            "stage_count": len(stages),
            "running": int(states.get("RUNNING", 0)),
            "errors": int(states.get("FAILED", 0) + states.get("INCOMPLETE", 0) + states.get("STALE", 0)),
            "complete": int(states.get("COMPLETE", 0)),
            "not_started": int(states.get("NOT_STARTED", 0)),
            "total_records": sum(int(stage["raw_count"]) for stage in stages),
            "total_raw": sum(int(stage["raw_count"]) for stage in stages),
            "last_activity": last_activity,
        },
        "stages": stages,
        "recent_activity": recent,
    }
