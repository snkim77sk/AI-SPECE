"""Fail-closed fixtures for the disposable PR359 parent/cgroup integration test.

Only fixture data, an accelerated clock, and fault injection live here. No
production entry point imports this module. The web keeps ownership of the real
admission, Popen, source singleton, supervisor, durable gate, and reap sequence.
"""
from __future__ import annotations

import datetime as dt
import gc
import json
import mmap
import os
from pathlib import Path
import sys
import time
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MIB = 1 << 20
CI_DATABASE = "postgresql://postgres:postgres@127.0.0.1:5432/g2b_vnext_test"
CONTROL = Path("/ci-control")
DISK = Path("/var/tmp/g2b-parent-4303-ci")
SCENARIOS = ("json", "xml", "rollback", "near_cap_json", "near_cap_xml", "no_progress")
PAGE_SIZE = 1000
PAGES = 3
TOTAL_ROWS = 2477


def authorized(environ=None):
    env = os.environ if environ is None else environ
    scenario = env.get("G2B_4303_SCENARIO", "")
    return bool(scenario in SCENARIOS and env.get("G2B_CI_PARENT_4303") == "1"
        and env.get("G2B_DATABASE_URL") == CI_DATABASE
        and env.get("G2B_TEST_MODE") == "0"
        and env.get("G2B_AUTO_SYNC_DISABLE") == "1"
        and env.get("G2B_256MIB_DYNAMIC_SOURCE_BUDGET") == "1"
        and env.get("G2B_APP_SCHEMA") == f"g2b_app_parent_4303_{scenario}"
        and env.get("G2B_BUDGET_SCHEMA") == f"g2b_budget_parent_4303_{scenario}"
        and not any(env.get(k) for k in ("G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY")))


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def cgroup_snapshot():
    import memory_guard
    stat = dict((key, int(value)) for key, value in
                (line.split() for line in Path("/sys/fs/cgroup/memory.stat").read_text().splitlines()))
    return {**memory_guard.container_budget_snapshot(),
            "file_dirty_bytes": stat.get("file_dirty", 0),
            "file_writeback_bytes": stat.get("file_writeback", 0)}


def baseline_contract(state):
    assert state["source"] == "cgroup-v2" and state["limit_bytes"] == 256 * MIB, state
    assert abs(state["current_bytes"] / MIB - 173) <= 2, state
    assert abs(state["effective_bytes"] / MIB - 115) <= 2, state
    assert state["file_bytes"] >= 32 * MIB and state["inactive_file_bytes"] >= 32 * MIB, state
    assert state["shmem_bytes"] < 4 * MIB, state
    assert state["file_dirty_bytes"] <= MIB and state["file_writeback_bytes"] <= MIB, state


def scenario_shape(scenario, mode):
    if mode == "shopping":
        return 999, 1
    return (1000, 1) if scenario.startswith("near_cap_") else (PAGE_SIZE, PAGES)


def scenario_total(scenario, mode):
    return 999 if mode == "shopping" else 1000 if scenario.startswith("near_cap_") else TOTAL_ROWS


def fixture_row(mode, page, index, day):
    if mode == "shopping":
        return {"dlvrReqNo": f"CI4303-S-{page}-{index}", "dlvrReqChgOrd": "0", "prdctSno": "1",
                "dlvrReqRcptDate": day.replace("-", ""), "dtilPrdctClsfcNo": "3911160302",
                "prdctNm": "LED 가로등기구", "dminsttNm": "CI TEST PUBLIC ORGANIZATION",
                "corpNm": "CI SYNTHETIC LIGHTING", "prdctQty": "1", "prdctUprc": "100000",
                "prdctAmt": "100000", "ci_note": "A" * 4096, "ci_unicode": "한글🙂"}
    return {"fyr": day[:4], "dbiz_cd": f"CI4303-B-{page:03d}-{index:04d}",
            "dbiz_nm": "가로등 LED 예산", "exe_ymd": day.replace("-", ""),
            "bdg_cash_amt": str(100000 + page * PAGE_SIZE + index),
            "wa_laf_cd": "4100000", "laf_cd": "4111000", "ci_note": "A" * 4096, "ci_unicode": "한글🙂"}


def write_fixture(path, fmt, mode, page, day, *, rows, pages, near_cap=False, total_rows=None):
    """Write one row at a time; no host-prewarmed data or prebuilt row list."""
    with path.open("wb") as stream:
        total = rows * pages if total_rows is None else total_rows
        if fmt == "json":
            if mode == "shopping":
                head = '{"response":{"header":{"resultCode":"00","resultMsg":"OK"},"body":{"totalCount":%d,"items":[' % total
                tail = b"]}}}"
            else:
                head = ('{"QWGJK":[{"head":[{"list_total_count":%d},'
                        '{"RESULT":{"CODE":"INFO-000","MESSAGE":"OK"}}]},{"row":[' % total)
                tail = b"]}]}"
            stream.write(head.encode())
        elif mode == "shopping":
            stream.write((f"<response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header>"
                          f"<body><totalCount>{total}</totalCount><items>").encode())
        else:
            stream.write((f"<QWGJK><head><list_total_count>{total}</list_total_count>"
                          "<RESULT><CODE>INFO-000</CODE><MESSAGE>OK</MESSAGE></RESULT></head>").encode())
        for index in range(rows):
            row = fixture_row(mode, page, index, day)
            if near_cap:
                row["ci_note"] = "A" * 15000
            if fmt == "json":
                if index:
                    stream.write(b",")
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode())
            else:
                tag = "item" if mode == "shopping" else "row"
                stream.write(f"<{tag}>".encode())
                for key, value in row.items():
                    stream.write(f"<{key}>{escape(value)}</{key}>".encode())
                stream.write(f"</{tag}>".encode())
        stream.write(tail if fmt == "json" else b"</items></body></response>" if mode == "shopping" else b"</QWGJK>")
        if near_cap:
            remaining = 16 * MIB - 1 - stream.tell()
            assert remaining >= 0
            while remaining:
                count = min(remaining, 64 * 1024)
                stream.write(b" " * count)
                remaining -= count
        stream.flush()
        os.fsync(stream.fileno())
        # Fixture reads must be charged to the tested cgroup, not stale writes.
        os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)


class Ballast:
    def __init__(self, disk=DISK):
        self.base = []
        self.pressure = []
        self.disk = disk
        disk.mkdir(parents=True, exist_ok=True)
        self.cache = (disk / "clean-cache.bin").open("w+b", buffering=0)
        self.block = b"C" * (64 * 1024)
        self.released_offset = 0

    def add_anon(self, size, owner):
        chunk = mmap.mmap(-1, size, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        for offset in range(0, size, mmap.PAGESIZE):
            chunk[offset] = 1
        owner.append(chunk)

    def add_cache(self, size):
        while size:
            count = min(size, len(self.block))
            self.cache.write(self.block[:count])
            size -= count
        os.fsync(self.cache.fileno())

    def settle(self):
        for _ in range(100):
            state = cgroup_snapshot()
            if state["file_dirty_bytes"] <= MIB and state["file_writeback_bytes"] <= MIB:
                return state
            time.sleep(.05)
        raise AssertionError("CI_CACHE_NEVER_CLEAN")

    def prepare(self):
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            left, right = line.split(" - ", 1)
            mountpoint = left.split()[4]
            if str(self.disk) == mountpoint or str(self.disk).startswith(mountpoint.rstrip("/") + "/"):
                assert right.split()[0] != "tmpfs", "CI_CACHE_MUST_BE_DISK"
        self.add_cache(32 * MIB)
        for _ in range(200):
            delta = 115 * MIB - self.settle()["effective_bytes"]
            if delta <= 256 * 1024:
                break
            self.add_anon(min(delta, MIB), self.base)
            time.sleep(.01)
        for _ in range(200):
            delta = 173 * MIB - cgroup_snapshot()["current_bytes"]
            if delta <= 256 * 1024:
                break
            self.add_cache(min(delta, MIB))
            time.sleep(.01)
        state = self.settle()
        baseline_contract(state)
        return state

    def drop_cache(self, size):
        # Real controlled-file eviction, never a fabricated memory.current.
        size = min(size, self.cache.tell() - self.released_offset)
        assert size > 0, "NO_CONTROLLED_CACHE_LEFT"
        os.fsync(self.cache.fileno())
        os.posix_fadvise(self.cache.fileno(), self.released_offset, size, os.POSIX_FADV_DONTNEED)
        self.released_offset += size
        time.sleep(.1)
        return self.settle()

    def lower_file_pressure(self, target):
        before = self.settle()
        for _ in range(100):
            state = cgroup_snapshot()
            if state["current_bytes"] <= target:
                assert before["current_bytes"] - state["current_bytes"] > 8 * MIB, (before, state)
                return state
            self.drop_cache(min(MIB, state["current_bytes"] - target))
        raise AssertionError("REAL_CACHE_REDUCTION_NOT_OBSERVED")

    def raise_file_pressure(self, target):
        assert target + 52 * MIB < 220_000_000
        for _ in range(100):
            delta = target - cgroup_snapshot()["current_bytes"]
            if delta <= 128 * 1024:
                return self.settle()
            self.add_cache(min(delta, MIB))
            time.sleep(.01)
        raise AssertionError("FILE_PRESSURE_TARGET_NOT_REACHED")

    def stabilize_cache(self, target):
        before = cgroup_snapshot()
        assert abs(before["current_bytes"] - target) < 8 * MIB, "BASELINE_CHANGED_MATERIALLY"
        for _ in range(100):
            current = cgroup_snapshot()["current_bytes"]
            delta = target - current
            if abs(delta) <= 256 * 1024:
                return self.settle()
            if delta > 0:
                self.add_cache(min(delta, MIB))
            else:
                self.drop_cache(min(-delta, MIB))
            time.sleep(.02)
        raise AssertionError("BASELINE_NOT_STABLE")

    def pressurize(self, target):
        assert target < 220_000_000
        for _ in range(300):
            state = cgroup_snapshot()
            delta = target - state["current_bytes"]
            if delta <= 64 * 1024:
                return state
            self.add_anon(min(delta, 256 * 1024), self.pressure)
            time.sleep(.01)
        raise AssertionError("CI_PRESSURE_TARGET_UNREACHED")

    def release(self):
        for chunk in self.pressure:
            chunk.close()
        self.pressure.clear()
        gc.collect()
        return cgroup_snapshot()
