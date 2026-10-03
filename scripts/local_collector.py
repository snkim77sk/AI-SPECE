"""Run AI-SPECE source collection on a local office computer and sync compact results.

Examples:
  python scripts/local_collector.py --server https://example.com --token <sync-token>
  python scripts/local_collector.py --server https://example.com --token <sync-token> --interval-minutes 120
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import pathlib
import sys
import time
from zoneinfo import ZoneInfo
import urllib.error
import urllib.request

# Allow direct execution as `python scripts/local_collector.py` from any working directory.
# Python otherwise puts only the scripts directory on sys.path, so project modules such
# as db.py and shopping_recent_vnext.py are not importable.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _parse_args():
    parser = argparse.ArgumentParser(description="AI-SPECE local collector/result sync")
    parser.add_argument(
        "--db",
        default=os.getenv("G2B_LOCAL_DB_PATH", "local_data/g2b-local.sqlite3"),
        help="local compatibility SQLite path",
    )
    parser.add_argument(
        "--g2b-key",
        default=os.getenv("G2B_SERVICE_KEY", ""),
        help="data.go.kr G2B service key",
    )
    parser.add_argument(
        "--server",
        default=os.getenv("G2B_RESULT_SERVER_URL", ""),
        help="Cafe24 result server base URL",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("G2B_RESULT_SYNC_TOKEN", ""),
        help="result sync token",
    )
    parser.add_argument(
        "--output",
        default=os.getenv("G2B_RESULT_SNAPSHOT_FILE", "local_data/result-snapshot.json.gz"),
        help="local compressed snapshot output",
    )
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument(
        "--start-date",
        default=os.getenv("G2B_LOCAL_START_DATE", "2026-09-01"),
        help="first shopping-delivery date to inspect/collect (YYYY-MM-DD; 4.1 default 2026-09-01)",
    )
    parser.add_argument(
        "--end-date",
        default=os.getenv("G2B_LOCAL_END_DATE", ""),
        help="optional inclusive last date; omitted means Korea D-1",
    )
    parser.add_argument("--interval-minutes", type=int, default=0)
    parser.add_argument(
        "--progress",
        action="store_true",
        help="print live day/page/classification progress",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run exactly one cycle even when --interval-minutes is set",
    )
    parser.add_argument("--max-days", type=int, default=62)
    return parser.parse_args()


KST = ZoneInfo("Asia/Seoul")


def _iso_date(value, name):
    text = str(value or "").strip()
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{name} must be YYYY-MM-DD") from None


def _collection_window(args, *, today=None):
    start = _iso_date(args.start_date, "--start-date")
    today = today or dt.datetime.now(KST).date()
    latest_allowed = today - dt.timedelta(days=1)
    end_text = str(args.end_date or "").strip()
    end = _iso_date(end_text, "--end-date") if end_text else latest_allowed
    if start > end:
        raise ValueError("--start-date must not be after --end-date")
    if end > latest_allowed:
        raise ValueError(
            f"--end-date must not be later than Korea D-1 ({latest_allowed.isoformat()})"
        )
    return start, end


class LocalDbProcessLock:
    """Cross-platform non-blocking process lock for one local SQLite database.

    The small lock file may remain on disk after exit; the operating-system lock,
    not file existence, decides ownership. This means a crash cannot leave a stale
    lock that permanently blocks the next collector run.
    """

    def __init__(self, db_path):
        path = pathlib.Path(str(db_path)).expanduser().resolve()
        self.path = pathlib.Path(str(path) + ".collector.lock")
        self._handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            handle.close()
            return False

        try:
            metadata = {
                "pid": os.getpid(),
                "locked_at_kst": dt.datetime.now(KST).isoformat(timespec="seconds"),
            }
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
            handle.flush()
            handle.seek(0)
        except Exception:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
            raise
        self._handle = handle
        return True

    def release(self):
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("LOCAL_COLLECTOR_ALREADY_RUNNING")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


def _kst_now():
    return dt.datetime.now(KST)


def _safe_error(stage, exc):
    return {
        "code": f"LOCAL_COLLECTOR_{str(stage or 'CYCLE').upper()}_FAILED",
        "type": type(exc).__name__,
    }


def _emit_result(result):
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def _console_progress(event):
    data = dict(event or {})
    name = str(data.get("event") or "")
    stamp = _kst_now().strftime("%H:%M:%S")
    date = str(data.get("date") or "")
    day_index = data.get("day_index")
    total_days = data.get("total_days")
    day = (
        f"[DAY {int(day_index)}/{int(total_days)}]"
        if day_index is not None and total_days is not None
        else "[DAY]"
    )

    if name == "run_start":
        print(
            f"[{stamp}] [RUN] range={data.get('start_date')}..{data.get('latest_date')} "
            f"days={data.get('total_days')}",
            flush=True,
        )
    elif name == "prepare_start":
        print(f"[{stamp}] [PREP] {data.get('stage')} START", flush=True)
    elif name == "prepare_complete":
        print(f"[{stamp}] [PREP] {data.get('stage')} COMPLETE", flush=True)
    elif name == "classification_deferred":
        print(
            f"[{stamp}] [CLASSIFY] DEFERRED until normalized batch completes",
            flush=True,
        )
    elif name == "classification_start":
        suffix = f" date={date}" if date else ""
        print(f"[{stamp}] [CLASSIFY] {data.get('stage')} START{suffix}", flush=True)
    elif name == "classification_complete":
        suffix = f" date={date}" if date else ""
        print(
            f"[{stamp}] [CLASSIFY] {data.get('stage')} COMPLETE "
            f"classified={int(data.get('classified') or 0)}{suffix}",
            flush=True,
        )
    elif name == "day_skipped":
        print(f"[{stamp}] {day} {date} SKIP already_complete", flush=True)
    elif name == "day_start":
        print(f"[{stamp}] {day} {date} START", flush=True)
    elif name == "scope_start":
        resumed = " resume=yes" if data.get("resumed") else ""
        print(
            f"[{stamp}] {day} {date} PAGE_START next={data.get('page')}{resumed}",
            flush=True,
        )
    elif name == "page_complete":
        page = int(data.get("page") or 0)
        total_pages = data.get("total_pages")
        saved = int(data.get("saved") or 0)
        total = data.get("source_total")
        page_text = f"{page}/{int(total_pages)}" if total_pages else str(page)
        if total:
            pct = min(100.0, (saved / int(total)) * 100.0)
            amount = f"{saved:,}/{int(total):,} ({pct:.1f}%)"
        else:
            amount = f"{saved:,}/unknown"
        print(
            f"[{stamp}] {day} {date} [PAGE {page_text}] saved={amount}",
            flush=True,
        )
    elif name in ("page_stopped", "page_failed"):
        code = str(data.get("error_code") or data.get("error_type") or "UNKNOWN")
        print(
            f"[{stamp}] {day} {date} {name.upper()} code={code}",
            flush=True,
        )
    elif name == "day_partial":
        print(
            f"[{stamp}] {day} {date} PARTIAL saved={int(data.get('saved') or 0):,}",
            flush=True,
        )
    elif name == "day_complete":
        total = data.get("source_total")
        total_text = f"/{int(total):,}" if total else ""
        print(
            f"[{stamp}] {day} {date} COMPLETE "
            f"saved={int(data.get('saved') or 0):,}{total_text}",
            flush=True,
        )
    elif name == "run_complete":
        print(
            f"[{stamp}] [RUN] {data.get('status')} attempted={int(data.get('attempted') or 0)}",
            flush=True,
        )
    elif name == "run_failed":
        print(
            f"[{stamp}] [RUN] FAILED type={data.get('error_type')}",
            flush=True,
        )


def _prepare_runtime(args):
    """Prepare the retired local compatibility collector on isolated SQLite only."""
    db_path = str(pathlib.Path(args.db).expanduser().resolve())
    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    os.environ["G2B_RUNTIME_ROLE"] = "LOCAL_COLLECTOR"
    os.environ["G2B_TEST_MODE"] = "1"
    os.environ["G2B_BUDGET_STORAGE"] = "sqlite"
    os.environ["G2B_DB_PATH"] = db_path
    os.environ["G2B_AUTO_SYNC"] = "0"
    for name in (
        "G2B_DATABASE_URL",
        "G2B_BUDGET_DATABASE_URL",
        "DATABASE_URL",
        "POSTGRES_URL",
        "POSTGRESQL_URL",
    ):
        os.environ.pop(name, None)
    if str(args.g2b_key or "").strip():
        os.environ["G2B_SERVICE_KEY"] = str(args.g2b_key).strip()


def _write_snapshot(payload, output):
    path = pathlib.Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=6)
    path.write_bytes(compressed)
    return str(path), len(raw), len(compressed)


def _push_snapshot(payload, server, token):
    base = str(server or "").strip().rstrip("/")
    secret = str(token or "").strip()
    if not base:
        return {"status": "LOCAL_ONLY", "reason": "RESULT_SERVER_URL_NOT_SET"}
    if len(secret) < 32:
        raise RuntimeError("result sync token must be at least 32 characters")

    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    body = gzip.compress(raw, compresslevel=6)
    req = urllib.request.Request(
        base + "/api/result-sync",
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + secret,
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "User-Agent": "AI-SPECE-LOCAL-COLLECTOR/3.2",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            content = response.read(1024 * 1024)
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"result server rejected snapshot: HTTP {exc.code}") from None
    if status != 200:
        raise RuntimeError(f"result server returned HTTP {status}")
    try:
        result = json.loads(content.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise RuntimeError("invalid result server response") from None
    return result


def _execute_cycle(args):
    """Run one collection/snapshot/sync cycle and return safe structured diagnostics."""
    started = _kst_now()
    result = {
        "status": "RUNNING",
        "run_started_at_kst": started.isoformat(timespec="seconds"),
        "run_finished_at_kst": "",
        "collection_window": None,
        "collection": None,
        "shopping_retention": None,
        "snapshot_generated": False,
        "snapshot_id": "",
        "snapshot_file": "",
        "snapshot_rows": 0,
        "raw_bytes": 0,
        "compressed_bytes": 0,
        "sync": None,
        "error": None,
    }
    stage = "prepare"
    failure = None
    try:
        _prepare_runtime(args)

        import db
        import result_snapshot_vnext
        import shopping_recent_vnext
        import shopping_store_v41
        import vnext_store

        db.init_db()
        vnext_store.ensure_foundation()

        stage = "collect"
        collection = None
        if not args.skip_collect:
            start_day, end_day = _collection_window(args)
            result["collection_window"] = {
                "start_date": start_day.isoformat(),
                "end_date": end_day.isoformat(),
            }
            collection = shopping_recent_vnext.collect_forward(
                start_date=start_day,
                latest_date=end_day,
                max_days=max(1, min(int(args.max_days), 62)),
                retention_days=365,
                progress=_console_progress if bool(getattr(args, "progress", False)) else None,
                defer_classification=True,
            )
        result["collection"] = collection

        # The retired local SQLite compatibility path obeys the same storage
        # retention policy as Cafe24. Run this even for --skip-collect so old
        # local data cannot leak into a newly generated result snapshot.
        stage = "retention"
        result["shopping_retention"] = shopping_store_v41.purge_history(
            365,
            now=started,
        )

        stage = "snapshot"
        if bool(getattr(args, "progress", False)):
            print(f"[{_kst_now().strftime('%H:%M:%S')}] [SNAPSHOT] START", flush=True)
        payload = result_snapshot_vnext.build_local_snapshot()
        path, raw_size, compressed_size = _write_snapshot(payload, args.output)
        result.update(
            snapshot_generated=True,
            snapshot_id=str(payload["snapshot_id"]),
            snapshot_file=path,
            snapshot_rows=sum(len(v) for v in payload["sections"].values()),
            raw_bytes=raw_size,
            compressed_bytes=compressed_size,
        )
        if bool(getattr(args, "progress", False)):
            print(
                f"[{_kst_now().strftime('%H:%M:%S')}] [SNAPSHOT] COMPLETE "
                f"rows={result['snapshot_rows']:,} compressed={compressed_size:,} bytes",
                flush=True,
            )

        stage = "sync"
        if bool(getattr(args, "progress", False)):
            print(f"[{_kst_now().strftime('%H:%M:%S')}] [SYNC] START", flush=True)
        result["sync"] = _push_snapshot(payload, args.server, args.token)
        if bool(getattr(args, "progress", False)):
            print(
                f"[{_kst_now().strftime('%H:%M:%S')}] [SYNC] "
                f"{str((result['sync'] or {}).get('status') or ('OK' if (result['sync'] or {}).get('ok') else 'DONE'))}",
                flush=True,
            )
        result["status"] = (
            str(collection.get("status") or "COMPLETE")
            if isinstance(collection, dict)
            else "COMPLETE"
        )
    except Exception as exc:
        failure = exc
        result["status"] = "FAILED"
        result["error"] = _safe_error(stage, exc)
    finally:
        result["run_finished_at_kst"] = _kst_now().isoformat(timespec="seconds")
    return result, failure


def _one_run(args):
    """Compatibility one-shot helper: preserve the old raise-on-error behavior."""
    result, failure = _execute_cycle(args)
    if failure is not None:
        raise failure
    return result


def _run_loop(args, *, cycle_fn=None, sleep_fn=None, emit_fn=None, max_cycles=None):
    cycle_fn = cycle_fn or _execute_cycle
    sleep_fn = sleep_fn or time.sleep
    emit_fn = emit_fn or _emit_result
    automatic = bool(int(args.interval_minutes or 0) > 0 and not args.once)
    cycles = 0

    while True:
        result, failure = cycle_fn(args)
        emit_fn(result)
        cycles += 1

        if not automatic:
            if failure is not None:
                raise failure
            return 0
        if max_cycles is not None and cycles >= int(max_cycles):
            return 0

        # Automatic mode is fail-soft: one failed source/snapshot/sync cycle is
        # reported safely and the next scheduled cycle retries from stored state.
        sleep_fn(max(5, int(args.interval_minutes)) * 60)


def main():
    args = _parse_args()
    db_path = str(pathlib.Path(args.db).expanduser().resolve())
    lock = LocalDbProcessLock(db_path)
    if not lock.acquire():
        now = _kst_now().isoformat(timespec="seconds")
        _emit_result({
            "status": "SKIPPED_ALREADY_RUNNING",
            "run_started_at_kst": now,
            "run_finished_at_kst": now,
            "collection_window": None,
            "collection": None,
            "snapshot_generated": False,
            "sync": None,
            "error": None,
        })
        return 0
    try:
        return _run_loop(args)
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
