"""Read-only collection progress monitor for the clean G2B vNext runtime.

This module never calls an external source and never starts, pauses, resumes, or
widens collection. It only summarizes existing RAW rows and collection checkpoints
so operators can see whether each collector is actually advancing.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import Counter

from db import connect
from vnext_store import ensure_foundation

RUNNING_STALE_SECONDS = 5 * 60

STAGES = (
    {
        "dataset": "bid_notice_goods",
        "number": "01",
        "label": "물품 입찰공고",
        "group": "나라장터",
        "live_gate": "VALIDATION_ONLY",
    },
    {
        "dataset": "bid_notice_service",
        "number": "02",
        "label": "용역 입찰공고",
        "group": "나라장터",
        "live_gate": "VALIDATION_ONLY",
    },
    {
        "dataset": "opening_result_service",
        "number": "03",
        "label": "용역 개찰 결과",
        "group": "나라장터",
        "live_gate": "VALIDATION_ONLY",
    },
    {
        "dataset": "award_result_service",
        "number": "04",
        "label": "용역 낙찰 결과",
        "group": "나라장터",
        "live_gate": "VALIDATION_ONLY",
    },
    {
        "dataset": "contract_service",
        "number": "05",
        "label": "용역 계약",
        "group": "나라장터",
        "live_gate": "VALIDATION_ONLY",
    },
    {
        "dataset": "shopping_delivery",
        "number": "06",
        "label": "쇼핑몰 납품요구",
        "group": "나라장터",
        "live_gate": "VALIDATION_ONLY",
    },
    {
        "dataset": "budget",
        "number": "07",
        "label": "지방재정365 세부사업·집행",
        "group": "예산",
        "live_gate": "VALIDATION_ONLY",
    },
    {
        "dataset": "budget_appropriation",
        "number": "08",
        "label": "지방재정365 세출예산",
        "group": "예산",
        "live_gate": "VALIDATION_ONLY",
    },
    {
        "dataset": "education_budget",
        "number": "09",
        "label": "교육청 예산",
        "group": "예산",
        "live_gate": "HOLD",
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


def _parse_sqlite_utc(value):
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


def _age_seconds(value, now):
    stamp = _parse_sqlite_utc(value)
    if stamp is None:
        return None
    return max(0.0, (now - stamp).total_seconds())


def _progress(row):
    if not row:
        return {
            "pages_processed": 0,
            "total_pages": None,
            "percent": None,
            "fetched_count": 0,
            "saved_count": 0,
            "source_total": None,
        }
    page_no = max(0, int(row.get("page_no") or 0))
    page_size = max(0, int(row.get("page_size") or 0))
    fetched = max(0, int(row.get("fetched_count") or 0))
    saved = max(0, int(row.get("saved_count") or 0))
    raw_total = int(row.get("source_total") or 0)
    source_total = raw_total if raw_total > 0 else None
    total_pages = (
        int(math.ceil(source_total / page_size))
        if source_total is not None and page_size > 0
        else None
    )
    percent = (
        min(100.0, round((fetched / source_total) * 100.0, 1))
        if source_total
        else None
    )
    return {
        "pages_processed": max(0, page_no - 1),
        "total_pages": total_pages,
        "percent": percent,
        "fetched_count": fetched,
        "saved_count": saved,
        "source_total": source_total,
    }


def _message(state, row, progress, raw_count):
    scope = str((row or {}).get("scope_key") or "").strip()
    scope_text = f"{scope} · " if scope else ""
    if state == "RUNNING":
        page = progress["pages_processed"]
        total_pages = progress["total_pages"]
        pages = f"{page}/{total_pages}페이지" if total_pages else f"{page}페이지"
        return f"{scope_text}{pages} 처리 · {progress['saved_count']:,}건 저장"
    if state == "COMPLETE":
        return (
            f"{scope_text}수집 완료 · {progress['saved_count']:,}건 저장"
            if row
            else f"현재 RAW {raw_count:,}건"
        )
    if state in {"FAILED", "INCOMPLETE"}:
        reason = str((row or {}).get("last_error") or "").strip() or "상세 오류 없음"
        return f"{scope_text}{reason}"
    if state == "STALE":
        return f"{scope_text}RUNNING 상태가 {RUNNING_STALE_SECONDS // 60}분 이상 갱신되지 않았습니다"
    if state == "DATA_ONLY":
        return f"현재 RAW {raw_count:,}건 · 실행 체크포인트 없음"
    if state == "IDLE":
        return "수집 대기 상태"
    return "아직 수집 실행 이력이 없습니다"


def _stage_snapshot(conn, spec, *, now):
    rows = [
        dict(row)
        for row in conn.execute(
            """SELECT dataset,scope_key,range_start,range_end,page_no,page_size,
                      source_total,fetched_count,saved_count,status,last_error,updated_at
               FROM collection_checkpoints
               WHERE dataset=?
               ORDER BY updated_at DESC, scope_key DESC""",
            (spec["dataset"],),
        ).fetchall()
    ]
    latest = rows[0] if rows else None
    active = []
    for row in rows:
        if str(row.get("status") or "") != "RUNNING":
            continue
        age = _age_seconds(row.get("updated_at"), now)
        if age is not None and age <= RUNNING_STALE_SECONDS:
            active.append(row)

    current = active[0] if active else latest
    raw_row = conn.execute(
        """SELECT COUNT(*) AS n,MAX(fetched_at) AS last_at
           FROM raw_records WHERE dataset=?""",
        (spec["dataset"],),
    ).fetchone()
    raw_count = int(raw_row["n"] or 0) if raw_row else 0
    raw_last_at = str(raw_row["last_at"] or "") if raw_row else ""

    if active:
        state = "RUNNING"
    elif latest:
        source_state = str(latest.get("status") or "IDLE")
        if source_state == "RUNNING":
            state = "STALE"
        elif source_state in {"COMPLETE", "FAILED", "INCOMPLETE", "IDLE"}:
            state = source_state
        else:
            state = "IDLE"
    elif raw_count:
        state = "DATA_ONLY"
    else:
        state = "NOT_STARTED"

    counts = Counter(str(row.get("status") or "IDLE") for row in rows)
    progress = _progress(current)
    last_activity = str((current or {}).get("updated_at") or raw_last_at or "")
    return {
        **spec,
        "state": state,
        "state_label": STATUS_LABELS[state],
        "scope": str((current or {}).get("scope_key") or ""),
        "range_start": str((current or {}).get("range_start") or ""),
        "range_end": str((current or {}).get("range_end") or ""),
        "last_activity": last_activity,
        "last_error": str((current or {}).get("last_error") or ""),
        "raw_count": raw_count,
        "checkpoint_count": len(rows),
        "complete_scopes": int(counts.get("COMPLETE", 0)),
        "running_scopes": len(active),
        "failed_scopes": int(counts.get("FAILED", 0)),
        "incomplete_scopes": int(counts.get("INCOMPLETE", 0)),
        "message": _message(state, current, progress, raw_count),
        **progress,
    }


def monitor_snapshot(*, recent_limit=30, now=None):
    """Return a factual snapshot of collector state without performing source I/O."""
    ensure_foundation()
    current = now or _utc_now()
    with connect() as conn:
        stages = [_stage_snapshot(conn, spec, now=current) for spec in STAGES]
        datasets = tuple(spec["dataset"] for spec in STAGES)
        placeholders = ",".join("?" for _ in datasets)
        activity = [
            dict(row)
            for row in conn.execute(
                f"""SELECT dataset,scope_key,page_no,page_size,source_total,
                           fetched_count,saved_count,status,last_error,updated_at
                    FROM collection_checkpoints
                    WHERE dataset IN ({placeholders})
                    ORDER BY updated_at DESC
                    LIMIT ?""",
                (*datasets, max(1, min(int(recent_limit), 100))),
            ).fetchall()
        ]

    label_by_dataset = {spec["dataset"]: spec["label"] for spec in STAGES}
    recent = []
    for row in activity:
        progress = _progress(row)
        recent.append(
            {
                "dataset": row["dataset"],
                "label": label_by_dataset.get(row["dataset"], row["dataset"]),
                "scope": str(row.get("scope_key") or ""),
                "status": str(row.get("status") or "IDLE"),
                "status_label": STATUS_LABELS.get(
                    str(row.get("status") or "IDLE"), str(row.get("status") or "IDLE")
                ),
                "pages_processed": progress["pages_processed"],
                "total_pages": progress["total_pages"],
                "fetched_count": progress["fetched_count"],
                "saved_count": progress["saved_count"],
                "last_error": str(row.get("last_error") or ""),
                "updated_at": str(row.get("updated_at") or ""),
            }
        )

    states = Counter(stage["state"] for stage in stages)
    last_activity = max(
        (str(stage["last_activity"]) for stage in stages if stage["last_activity"]),
        default="",
    )
    return {
        "generated_at_utc": current.isoformat(),
        "refresh_hint_seconds": 5,
        "monitor_scope": "READ_ONLY_CHECKPOINT_AND_RAW_STATE",
        "source_io_performed": False,
        "collection_controls_enabled": False,
        "safety": {
            "bulk_historical_hold": True,
            "approved_historical_context_available": False,
            "education_live_transport_hold": True,
        },
        "summary": {
            "stage_count": len(stages),
            "running": int(states.get("RUNNING", 0)),
            "errors": int(states.get("FAILED", 0) + states.get("INCOMPLETE", 0) + states.get("STALE", 0)),
            "complete": int(states.get("COMPLETE", 0)),
            "not_started": int(states.get("NOT_STARTED", 0)),
            "total_raw": sum(int(stage["raw_count"]) for stage in stages),
            "last_activity": last_activity,
        },
        "stages": stages,
        "recent_activity": recent,
    }
