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
import time
from zoneinfo import ZoneInfo
import urllib.error
import urllib.request


def _parse_args():
    parser = argparse.ArgumentParser(description="AI-SPECE local collector/result sync")
    parser.add_argument(
        "--db",
        default=os.getenv("G2B_LOCAL_DB_PATH", "local_data/g2b-local.sqlite3"),
        help="local RAW SQLite path",
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
        help="first shopping-delivery date to inspect/collect (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        default=os.getenv("G2B_LOCAL_END_DATE", ""),
        help="optional inclusive last date; omitted means Korea D-1",
    )
    parser.add_argument("--interval-minutes", type=int, default=0)
    parser.add_argument("--max-days", type=int, default=31)
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


def _prepare_runtime(args):
    db_path = str(pathlib.Path(args.db).expanduser().resolve())
    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    os.environ["G2B_RUNTIME_ROLE"] = "LOCAL_COLLECTOR"
    os.environ["G2B_DB_PATH"] = db_path
    os.environ["G2B_AUTO_SYNC"] = "0"
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


def _one_run(args):
    _prepare_runtime(args)

    import db
    import result_snapshot_vnext
    import shopping_recent_vnext
    import vnext_store

    db.init_db()
    vnext_store.ensure_foundation()

    collection = None
    collection_window = None
    if not args.skip_collect:
        start_day, end_day = _collection_window(args)
        collection_window = {
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
        }
        collection = shopping_recent_vnext.collect_forward(
            start_date=start_day,
            latest_date=end_day,
            max_days=max(1, min(int(args.max_days), 31)),
        )

    payload = result_snapshot_vnext.build_local_snapshot()
    path, raw_size, compressed_size = _write_snapshot(payload, args.output)
    pushed = _push_snapshot(payload, args.server, args.token)
    return {
        "collection": collection,
        "collection_window": collection_window,
        "snapshot_id": payload["snapshot_id"],
        "snapshot_file": path,
        "snapshot_rows": sum(len(v) for v in payload["sections"].values()),
        "raw_bytes": raw_size,
        "compressed_bytes": compressed_size,
        "sync": pushed,
    }


def main():
    args = _parse_args()
    while True:
        result = _one_run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        if args.interval_minutes <= 0:
            return 0
        time.sleep(max(5, int(args.interval_minutes)) * 60)


if __name__ == "__main__":
    raise SystemExit(main())
