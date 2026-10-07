"""Disposable result-sync worker for low-memory Cafe24 deployments.

The web process only authenticates and spools a bounded compressed upload to disk.
JSON decompression/parsing and SQLite result import happen here, outside the
long-lived web process. If memory pressure becomes extreme, this process is biased
to be killed before the web server.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import sys
from pathlib import Path

MAX_COMPRESSED_BYTES = 4 * 1024 * 1024
MAX_JSON_BYTES = 12 * 1024 * 1024


def _prepare_environment():
    # The child must never start source work or a destructive bootstrap.
    os.environ["G2B_RUNTIME_ROLE"] = "LOCAL_COLLECTOR"
    os.environ["G2B_AUTO_SYNC"] = "0"
    os.environ["G2B_AUTO_SYNC_DISABLE"] = "1"
    os.environ["G2B_POST_BOOT_MAINTENANCE_ENABLE"] = "0"
    os.environ["G2B_MATCH_ROLLOVER_AUTO_ENABLE"] = "0"
    os.environ["G2B_V41_FRESH_START"] = "0"

    requested = str(
        os.environ.get("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB", "112") or "112"
    )
    try:
        requested_int = max(96, min(160, int(requested)))
    except (TypeError, ValueError):
        requested_int = 112
    os.environ["G2B_MEMORY_SOFT_LIMIT_MB"] = str(requested_int)


def _decode_snapshot_file(path, encoding):
    source = Path(path)
    size = int(source.stat().st_size)
    if size > MAX_COMPRESSED_BYTES:
        raise ValueError("COMPRESSED_SNAPSHOT_TOO_LARGE")

    body = source.read_bytes()
    if str(encoding or "").lower().strip() == "gzip":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body), mode="rb") as handle:
                raw = handle.read(MAX_JSON_BYTES + 1)
        except OSError:
            raise ValueError("INVALID_GZIP_SNAPSHOT") from None
    else:
        raw = body

    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("SNAPSHOT_JSON_TOO_LARGE")

    # Drop the compressed buffer before Python builds the nested JSON object.
    if raw is not body:
        del body
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise ValueError("INVALID_SNAPSHOT_JSON") from None
    del raw

    try:
        payload = json.loads(text)
    except ValueError:
        raise ValueError("INVALID_SNAPSHOT_JSON") from None
    del text
    return payload


def _process_snapshot_file(input_path, encoding, result_path):
    payload = _decode_snapshot_file(input_path, encoding)

    import memory_guard

    state = memory_guard.snapshot(collect=True)
    if not bool(state.get("process_guard_ok", True)):
        raise memory_guard.MemoryPressureError("PROCESS_RSS_HOLD_AFTER_PARSE")
    if bool(state.get("cgroup_blocked", False)):
        raise memory_guard.MemoryPressureError("CGROUP_BLOCK_AFTER_PARSE")

    import result_snapshot_vnext

    manifest = result_snapshot_vnext.import_snapshot(payload)
    Path(result_path).write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return manifest


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        return 2
    input_path, encoding, result_path = args

    _prepare_environment()

    import memory_guard
    from g2b_heavy_worker import _deprioritize, _start_parent_watchdog, _worker_slot

    memory_guard.apply_default_process_tuning()
    _deprioritize()
    _start_parent_watchdog()

    try:
        with _worker_slot():
            state = memory_guard.snapshot(collect=True)
            if int(state.get("cgroup_oom_group") or 0) == 1:
                print("G2B_RESULT_SYNC_WORKER_HOLD OOM_GROUP", flush=True)
                return 76
            memory_guard.wait_for_heavy_work_budget(timeout=15.0)
            _process_snapshot_file(input_path, encoding, result_path)
            print("G2B_RESULT_SYNC_WORKER_OK", flush=True)
            return 0
    except memory_guard.MemoryPressureError as exc:
        print("G2B_RESULT_SYNC_WORKER_MEMORY_HOLD", str(exc), flush=True)
        return 75
    except ValueError as exc:
        print("G2B_RESULT_SYNC_WORKER_INVALID", str(exc)[:120], flush=True)
        return 73
    except Exception as exc:
        print("G2B_RESULT_SYNC_WORKER_ERROR", type(exc).__name__, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
