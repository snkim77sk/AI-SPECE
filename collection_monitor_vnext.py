"""Read-only collection monitor for G2B 4.x.

The product has four source stages only:
- shopping delivery requests (stored only for lighting/poles from 2026-01-01),
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
from vnext_source_guard import MAX_OPERATIONAL_BUDGET_AGE_DAYS

RUNNING_STALE_SECONDS = 5 * 60
_KST = dt.timezone(dt.timedelta(hours=9))
SHOPPING_BOOTSTRAP_START_DATE = dt.date(2026, 1, 1)
SHOPPING_RETENTION_DAYS_DEFAULT = 365
SHOPPING_RETENTION_MONTHS_DEFAULT = 27


def _shopping_retention_days():
    try:
        value = int(
            str(
                os.getenv(
                    "G2B_SHOPPING_RETENTION_DAYS",
                    str(SHOPPING_RETENTION_DAYS_DEFAULT),
                )
                or SHOPPING_RETENTION_DAYS_DEFAULT
            ).strip()
        )
    except (TypeError, ValueError):
        value = SHOPPING_RETENTION_DAYS_DEFAULT
    return max(30, min(value, 365))


def _shopping_retention_months():
    try:
        value = int(
            str(
                os.getenv(
                    "G2B_SHOPPING_RETENTION_MONTHS",
                    str(SHOPPING_RETENTION_MONTHS_DEFAULT),
                )
                or SHOPPING_RETENTION_MONTHS_DEFAULT
            ).strip()
        )
    except (TypeError, ValueError):
        value = SHOPPING_RETENTION_MONTHS_DEFAULT
    return max(1, min(value, 27))


def _shopping_window_start(now):
    stamp = now or _utc_now()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    current_day = stamp.astimezone(_KST).date()
    floor = shopping_store_v41.retention_cutoff_date(
        retention_months=_shopping_retention_months(),
        now=current_day,
    )
    return max(SHOPPING_BOOTSTRAP_START_DATE, floor)


STAGES = (
    {
        "dataset": "shopping_delivery",
        "number": "01",
        "label": "조명·등주 쇼핑몰 납품요구",
        "group": "나라장터",
        "live_gate": "OPERATIONAL · BOOTSTRAP_2026-01-01 · RETENTION_27M · NORMALIZED_TARGET_ONLY",
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
        "group": "예산",
        "live_gate": "OPERATIONAL_BUDGET · CURRENT_YEAR_BASE + NEXT_YEAR_REFRESH",
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
    "PARTIAL": "부분완료",
    "PARTITION_COMPLETE": "지역분할완료",
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
        if status == "PARTITION_COMPLETE":
            return "COMPLETE"
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


def _shopping_stage(conn, spec, now, recent_limit=30):
    limit = max(1, min(int(recent_limit), 100))
    rows = [
        dict(row) for row in conn.execute(
            """SELECT dataset,scope_key,range_start,range_end,page_no,page_size,
                      source_total,fetched_count,saved_count,status,last_error,updated_at
               FROM collection_checkpoints WHERE dataset=?
               ORDER BY updated_at DESC,scope_key DESC
               LIMIT ?""",
            (spec["dataset"], limit),
        ).fetchall()
    ]
    latest = rows[0] if rows else None
    status_rows = conn.execute(
        """SELECT status,COUNT(*) AS n
           FROM collection_checkpoints
           WHERE dataset=?
           GROUP BY status""",
        (spec["dataset"],),
    ).fetchall()
    counts = Counter({
        str(row["status"] or "IDLE"): int(row["n"] or 0)
        for row in status_rows
    })
    checkpoint_count = sum(int(value or 0) for value in counts.values())
    test_mode = str(os.getenv("G2B_TEST_MODE", "0") or "").lower() in {
        "1", "true", "yes", "on"
    }
    local_collector = False
    if test_mode:
        import runtime_role
        local_collector = runtime_role.is_local_collector()
    if test_mode and not local_collector:
        raw_row = conn.execute(
            "SELECT COUNT(*) n,MAX(fetched_at) last_at FROM raw_records WHERE dataset=?",
            (spec["dataset"],),
        ).fetchone()
        raw_count = int(raw_row["n"] or 0) if raw_row else 0
        history_count = raw_count
        inactive_count = 0
    else:
        raw_row = conn.execute(
            """SELECT COUNT(*) history_n,
                      SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) active_n,
                      SUM(CASE WHEN is_active=0 THEN 1 ELSE 0 END) inactive_n,
                      MAX(updated_at) last_at
               FROM shopping_records"""
        ).fetchone()
        raw_count = int(raw_row["active_n"] or 0) if raw_row else 0
        history_count = int(raw_row["history_n"] or 0) if raw_row else 0
        inactive_count = int(raw_row["inactive_n"] or 0) if raw_row else 0
    state = _state_for(latest, history_count, now)
    progress = _progress(latest)
    message = _stage_message(state, latest, progress, raw_count)
    if state == "DATA_ONLY" and history_count != raw_count:
        message = (
            f"현재 유효 {raw_count:,}건 · 보존 이력 {history_count:,}건"
        )
    return {
        **spec,
        "state": state,
        "state_label": STATUS_LABELS.get(state, state),
        "scope": str((latest or {}).get("scope_key") or ""),
        "range_start": str((latest or {}).get("range_start") or ""),
        "range_end": str((latest or {}).get("range_end") or ""),
        "last_activity": str((latest or {}).get("updated_at") or (raw_row["last_at"] if raw_row else "") or ""),
        "last_error": str((latest or {}).get("last_error") or ""),
        # raw_count remains the monitor's compatibility field, but now means
        # currently active normalized shopping rows in production.
        "raw_count": raw_count,
        "active_count": raw_count,
        "history_count": history_count,
        "inactive_count": inactive_count,
        "checkpoint_count": checkpoint_count,
        "complete_scopes": int(counts.get("COMPLETE", 0)),
        "running_scopes": int(counts.get("RUNNING", 0)),
        "failed_scopes": int(counts.get("FAILED", 0)),
        "incomplete_scopes": int(counts.get("INCOMPLETE", 0)),
        "message": message,
        **progress,
    }, rows


BUDGET_HISTORY_START_DATE = dt.date(2026, 1, 1)


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
    current_day = stamp.astimezone(_KST).date()
    latest = current_day - dt.timedelta(days=1)
    try:
        configured_days = int(
            str(os.getenv("G2B_BUDGET_RETENTION_DAYS", "365") or "365")
        )
    except (TypeError, ValueError):
        configured_days = 365
    window_days = min(
        max(30, min(configured_days, 730)),
        int(MAX_OPERATIONAL_BUDGET_AGE_DAYS),
    )
    start = max(
        BUDGET_HISTORY_START_DATE,
        current_day - dt.timedelta(days=window_days),
    )
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


def _current_budget_scope_parts(scope_key):
    parts = str(scope_key or "").split(":")
    if len(parts) == 2:
        region = ""
    elif len(parts) == 3 and parts[0] != "history":
        region = str(parts[2] or "").strip()
    else:
        return None
    try:
        year = int(parts[0])
        day = dt.date.fromisoformat(parts[1])
    except (TypeError, ValueError):
        return None
    if day.year != year:
        return None
    return year, day, region


def _budget_partition_progress(scopes):
    """Summarize active QWGJK regional fallback from checkpoints only."""
    rows = list(scopes or ())
    candidates = {}
    nationwide = {}

    for row in rows:
        parsed = _current_budget_scope_parts(row.get("scope_key"))
        if parsed is None:
            continue
        year, day, region = parsed
        key = (year, day)
        if region:
            candidates.setdefault(key, []).append(dict(row))
            continue

        nationwide[key] = dict(row)
        status = str(row.get("status") or "").upper()
        error = str(row.get("last_error") or "")
        if (
            status == "PARTITION_COMPLETE"
            or "REPEATED_OR_OVERLAPPING_PAGE_REPLAY_EXHAUSTED" in error
            or error.startswith("REGION_PARTITION_PLAN_COMPLETE:")
        ):
            candidates.setdefault(key, [])

    if not candidates:
        return {}

    def activity_key(key):
        grouped = list(candidates.get(key) or ())
        if key in nationwide:
            grouped.append(nationwide[key])
        return max(
            (str(row.get("updated_at") or "") for row in grouped),
            default="",
        )

    target = max(candidates, key=lambda key: (activity_key(key), key[1]))
    year, day = target
    region_rows = {}
    for row in candidates.get(target) or ():
        parsed = _current_budget_scope_parts(row.get("scope_key"))
        if parsed is None:
            continue
        code = str(parsed[2] or "")
        previous = region_rows.get(code)
        if (
            previous is None
            or str(row.get("updated_at") or "")
            >= str(previous.get("updated_at") or "")
        ):
            region_rows[code] = row

    try:
        import budget_vnext
        plan = dict(budget_vnext.operational_region_partition_plan(year) or {})
    except Exception as exc:
        plan = {
            "ready": False,
            "reason": "REGION_PLAN_STATUS_" + type(exc).__name__,
            "region_codes": [],
            "region_names": {},
            "region_count": 0,
        }

    plan_codes = [
        str(value or "").strip()
        for value in (plan.get("region_codes") or [])
        if str(value or "").strip()
    ]
    names = {
        str(key): str(value or "")
        for key, value in dict(plan.get("region_names") or {}).items()
    }
    observed_codes = sorted(region_rows)
    codes = plan_codes or observed_codes
    total = max(
        int(plan.get("region_count") or 0),
        len(codes),
        len(observed_codes),
    )
    completed = sum(
        str(region_rows.get(code, {}).get("status") or "").upper()
        == "COMPLETE"
        for code in codes
    )

    nationwide_row = nationwide.get(target) or {}
    partition_complete = (
        str(nationwide_row.get("status") or "").upper()
        == "PARTITION_COMPLETE"
    )
    if partition_complete and total > 0:
        completed = total

    active_candidates = [
        (code, row)
        for code, row in region_rows.items()
        if str(row.get("status") or "").upper() != "COMPLETE"
    ]
    active_candidates.sort(
        key=lambda item: str(item[1].get("updated_at") or ""),
        reverse=True,
    )
    active_code = active_candidates[0][0] if active_candidates else ""
    active_row = active_candidates[0][1] if active_candidates else None
    active_name = names.get(active_code, "") if active_code else ""
    active_label = active_name or active_code
    active_state = str((active_row or {}).get("status") or "").upper()
    active_progress = _progress(active_row)

    percent = (
        round(min(100.0, (completed / total) * 100.0), 1)
        if total > 0 else 0.0
    )

    if partition_complete:
        state = "COMPLETE"
        message = f"지역분할 완료 · {completed:,}/{total:,} 지역"
    elif active_state == "RUNNING":
        state = "RUNNING"
        message = (
            f"지역분할 수집중 · {completed:,}/{total:,} 지역 완료 · "
            f"현재 {active_label or '지역 확인 중'}"
        )
    elif active_state in {"FAILED", "INCOMPLETE"}:
        state = active_state
        message = (
            f"지역분할 확인 필요 · {completed:,}/{total:,} 지역 완료 · "
            f"현재 {active_label or '지역 확인 중'}"
        )
    elif bool(plan.get("ready")):
        state = "PARTIAL"
        message = (
            f"지역분할 준비 · {completed:,}/{total:,} 지역 완료 · "
            "다음 지역 자동 재개"
        )
    else:
        state = "INCOMPLETE"
        minimum = int(plan.get("minimum_regions") or 17)
        message = (
            f"지역분할 준비중 · 지역계획 {len(plan_codes):,}/{minimum:,} 확인"
        )

    last_activity = max(
        str((active_row or {}).get("updated_at") or ""),
        str(nationwide_row.get("updated_at") or ""),
    )
    last_error = (
        str((active_row or {}).get("last_error") or "")
        if state in {"FAILED", "INCOMPLETE"}
        else ""
    )
    return {
        "partition_mode": True,
        "partition_snapshot_date": day.isoformat(),
        "partition_year": year,
        "partition_plan_ready": bool(plan.get("ready")),
        "partition_plan_reason": str(plan.get("reason") or ""),
        "partition_total_regions": total,
        "partition_complete_regions": completed,
        "partition_percent": percent,
        "partition_active_region_code": active_code,
        "partition_active_region_name": active_name,
        "partition_active_region_label": active_label,
        "partition_active_state": active_state,
        "partition_region_names": names,
        "partition_message": message,
        "partition_state": state,
        "partition_last_activity": last_activity,
        "partition_last_error": last_error,
        "partition_active_pages": int(active_progress.get("pages_processed") or 0),
        "partition_active_total_pages": active_progress.get("total_pages"),
        "partition_active_saved": int(active_progress.get("saved_count") or 0),
    }


def _budget_scope_display(dataset, scope_key, region_names=None):
    scope = str(scope_key or "")
    if str(dataset) != "budget":
        return scope
    parts = scope.split(":")
    names = dict(region_names or {})
    if len(parts) >= 3 and parts[0] == "history":
        return "과거이력 · " + str(parts[2] or "")
    parsed = _current_budget_scope_parts(scope)
    if parsed is None:
        return scope
    _year, day, region = parsed
    if region:
        return (
            "지역분할 · "
            + str(names.get(region) or region)
            + " · "
            + day.isoformat()
        )
    return "전국 현재 · " + day.isoformat()


def _aidfa_year_statuses(scopes, now):
    stamp = now or _utc_now()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    current_year = stamp.astimezone(_KST).date().year
    roles = (
        (current_year, "현재연도 기초편성"),
        (current_year + 1, "다음연도 미래예산"),
    )
    rows = list(scopes or ())
    result = []
    for year, role in roles:
        matches = [
            row for row in rows
            if str(row.get("range_start") or "") == str(year)
            or str(row.get("scope_key") or "").startswith(f"{year}:")
        ]
        matches.sort(
            key=lambda row: (
                str(row.get("updated_at") or ""),
                str(row.get("scope_key") or ""),
            ),
            reverse=True,
        )
        latest = matches[0] if matches else None
        state = _state_for(latest, 0, stamp) if latest else "NOT_STARTED"
        progress = _progress(latest)
        result.append({
            "year": year,
            "role": role,
            "state": state,
            "state_label": STATUS_LABELS.get(state, state),
            "scope": str((latest or {}).get("scope_key") or ""),
            "last_activity": str((latest or {}).get("updated_at") or ""),
            "last_error": str((latest or {}).get("last_error") or ""),
            **progress,
        })
    return result


def _aidfa_combined_state(items):
    states = {str(item.get("state") or "NOT_STARTED") for item in items}
    for severe in ("FAILED", "INCOMPLETE", "STALE"):
        if severe in states:
            return severe
    if "RUNNING" in states:
        return "RUNNING"
    if states == {"COMPLETE"}:
        return "COMPLETE"
    if "COMPLETE" in states:
        return "PARTIAL"
    if states == {"NOT_STARTED"}:
        return "NOT_STARTED"
    return "PARTIAL"


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
    aidfa_years = (
        _aidfa_year_statuses(scopes, now)
        if str(spec.get("dataset") or "") == "budget_appropriation"
        else []
    )
    partition = (
        _budget_partition_progress(scopes)
        if str(spec.get("dataset") or "") == "budget"
        else {}
    )
    if aidfa_years:
        state = _aidfa_combined_state(aidfa_years)
    if partition:
        state = str(partition.get("partition_state") or state)
    return {
        **spec,
        **history_progress,
        **partition,
        "aidfa_years": aidfa_years,
        "state": state,
        "state_label": STATUS_LABELS.get(state, state),
        "scope": (
            "지역분할 · " + str(partition.get("partition_snapshot_date") or "")
            if partition
            else str((latest or {}).get("scope_key") or "")
        ),
        "range_start": str((latest or {}).get("range_start") or ""),
        "range_end": str((latest or {}).get("range_end") or ""),
        "last_activity": (
            str(partition.get("partition_last_activity") or "")
            if partition
            else str((latest or {}).get("updated_at") or "")
        ),
        "last_error": (
            str(partition.get("partition_last_error") or "")
            if partition
            else str((latest or {}).get("last_error") or "")
        ),
        "raw_count": raw_count,
        "raw_revisions": int(dataset_status.get("raw_revisions") or 0),
        "raw_backend": str(dataset_status.get("raw_backend") or ""),
        "checkpoint_count": int(dataset_status.get("checkpoint_count") or 0),
        "complete_scopes": (
            int(dataset_status.get("verified_complete_scopes") or 0)
            + int(dataset_status.get("compacted_complete_scopes") or 0)
            + int(dataset_status.get("partition_complete_scopes") or 0)
        ),
        "running_scopes": int((dataset_status.get("checkpoint_status_counts") or {}).get("RUNNING", 0)),
        "failed_scopes": int((dataset_status.get("checkpoint_status_counts") or {}).get("FAILED", 0)),
        "incomplete_scopes": int((dataset_status.get("checkpoint_status_counts") or {}).get("INCOMPLETE", 0)),
        "message": (
            str(partition.get("partition_message") or "")
            if partition
            else (
                " · ".join(
                    f"{item['year']} {item['role']} {item['state_label']}"
                    for item in aidfa_years
                )
                if aidfa_years
                else _stage_message(state, latest, progress, raw_count)
            )
        ),
        **progress,
    }, scopes


def monitor_snapshot(*, recent_limit=30, now=None):
    # Production startup already installs these schemas. The 5-second monitor is
    # a read path and must not repeat DDL/index checks while collectors are writing.
    test_mode = str(os.getenv("G2B_TEST_MODE", "0") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if test_mode:
        ensure_foundation()
        shopping_store_v41.ensure_schema()
    current = now or _utc_now()

    with connect() as conn:
        shopping, shopping_rows = _shopping_stage(
            conn, STAGES[0], current, recent_limit=recent_limit
        )

    try:
        budget_status = budget_collection_status_vnext.budget_collection_monitor_status()
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
                "scope_display": _budget_scope_display(
                    spec["dataset"],
                    row.get("scope_key"),
                    stage.get("partition_region_names") or {},
                ),
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
            "start_date": _shopping_window_start(current).isoformat(),
            "bootstrap_start_date": SHOPPING_BOOTSTRAP_START_DATE.isoformat(),
            "retention_days": _shopping_retention_days(),
            "retention_months": _shopping_retention_months(),
            "retention_policy": "CALENDAR_MONTHS",
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
            "shopping_active_records": int(shopping.get("active_count") or 0),
            "shopping_history_records": int(shopping.get("history_count") or 0),
            "shopping_inactive_records": int(shopping.get("inactive_count") or 0),
            "last_activity": last_activity,
        },
        "stages": stages,
        "recent_activity": recent,
    }
