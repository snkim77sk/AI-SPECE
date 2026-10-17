"""Disposable web entry point. Child lifecycle remains owned by the actual web.

A private bind-mounted file mailbox drives the real scheduler and fault hooks;
there is no HTTP admin endpoint and no code/env hook in production modules.
"""
from __future__ import annotations

import datetime as dt
import errno
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time
from zoneinfo import ZoneInfo

from g2b_4303_parent_fixture_ci import (ROOT, CONTROL, DISK, MIB, Ballast,
    authorized, cgroup_snapshot, scenario_shape, scenario_total, write_fixture, write_json)


class Clock:
    """Only the app's clock reference changes; kernel/HTTP/wait time is real."""
    def __init__(self):
        self.offset = 0.0
        self.day = dt.date.fromisoformat(os.environ["G2B_4303_SNAPSHOT_DATE"])

    def monotonic(self):
        return time.monotonic() + self.offset

    def __getattr__(self, name):
        return getattr(time, name)

    def wall(self):
        return dt.datetime.combine(self.day, dt.time.min, tzinfo=ZoneInfo("Asia/Seoul")) + dt.timedelta(seconds=self.offset)


def controller(app):
    import g2b_dynamic_source_budget as dynamic
    import g2b_idle_memory_recovery as recovery
    import memory_guard

    assert dynamic.START_RESERVE_BYTES == 52 * MIB
    assert dynamic.SOURCE_CEILING_BYTES == 220_000_000
    clock = Clock()
    app.time = clock
    # Advance the boot-only grace clock without deleting/replacing its guard.
    app._WEB_FIRST_BOOT_MONOTONIC = clock.monotonic() - 301
    original_slot = app._small_cgroup_source_stagger_slot
    app._small_cgroup_source_stagger_slot = lambda now=None: original_slot(now or clock.wall())
    ballast = Ballast()
    events = []
    cycle_thread = None
    cycle_result = None
    original_launch = app._launch_isolated_heavy_worker_locked
    original_wait = app._wait_for_isolated_heavy_child

    def record(event, **values):
        row = {"event": event, "parent_pid": os.getpid(), "time_ns": time.time_ns(), **values}
        events.append(row)
        assert len(events) <= 64, "UNBOUNDED_FIXTURE_LIFECYCLE_LOG"
        with (CONTROL / "lifecycle.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")

    def launch(mode):
        assert app._ISOLATED_HEAVY_PROCESS is None, "REAL_PARENT_SLOT_OVERLAP"
        record("launch_attempt", mode=mode, snapshot=cgroup_snapshot())
        child = original_launch(mode)
        if child is not None:
            record("spawned", mode=mode, child_pid=child.pid,
                   phase=os.environ["G2B_4303_PHASE"], snapshot=cgroup_snapshot())
        return child

    def wait(process, mode):
        result = original_wait(process, mode)
        assert process.returncode is not None, "PARENT_WAIT_DID_NOT_REAP"
        assert not Path(f"/proc/{process.pid}").exists(), "REAP_LEFT_ZOMBIE"
        record("reaped", mode=mode, child_pid=process.pid,
               exit_code=result[0], parent_memory_abort=result[1], snapshot=cgroup_snapshot())
        return result

    app._launch_isolated_heavy_worker_locked = launch
    app._wait_for_isolated_heavy_child = wait
    write_json(CONTROL / "parent-ready.json", {"web_pid": os.getpid(),
        "cgroup": Path("/proc/self/cgroup").read_text(),
        "startup_reserve_bytes": dynamic.START_RESERVE_BYTES,
        "real_parent_entry": "run.py -> main:app -> vnext_clean_app",
        "clock": "accelerated app clock; unmodified scheduling and admission"})

    def run_cycle():
        nonlocal cycle_result
        original_auto = app._auto_sync_enabled
        app._auto_sync_enabled = lambda: True
        try:
            cycle_result = app._run_low_memory_automatic_cycle()
        except BaseException as exc:
            cycle_result = {"error": repr(exc)}
        finally:
            app._auto_sync_enabled = original_auto

    try:
        while True:
            pressure = CONTROL / "pressure-request.json"
            if pressure.exists():
                request = json.loads(pressure.read_text())
                pressure.unlink()
                current = app._ISOLATED_HEAVY_PROCESS
                assert current is not None and current.pid == request["pid"]
                pressure_before = sum(map(len, ballast.pressure))
                snapshot = ballast.pressurize(request["target"])
                write_json(CONTROL / "pressure-ready.json", {**snapshot,
                    "injected_target_bytes": request["target"],
                    "injected_pressure_bytes": sum(map(len, ballast.pressure)) - pressure_before})
            path = CONTROL / "command.json"
            if not path.exists():
                time.sleep(.01)
                continue
            command = json.loads(path.read_text())
            path.unlink()
            op = command["op"]
            result = {"op": op}
            if op == "prepare":
                scenario = os.environ["G2B_4303_SCENARIO"]
                fmt = "xml" if "xml" in scenario or scenario == "rollback" else "json"
                for mode in ("shopping", "budget"):
                    (DISK / mode).mkdir(exist_ok=True)
                    size, pages = scenario_shape(scenario, mode)
                    total = scenario_total(scenario, mode)
                    day = clock.day if mode == "budget" else clock.day - dt.timedelta(days=1)
                    for page in range(1, pages + 1):
                        write_fixture(DISK / mode / f"page-{page}.{fmt}", fmt, mode, page,
                            day.isoformat(), rows=min(size, total - (page - 1) * size), pages=pages, total_rows=total,
                            near_cap=scenario.startswith("near_cap_") and mode == "budget")
                result["snapshot"] = ballast.prepare()
            elif op == "reclaim":
                mode = command["mode"]
                result["fault_mode"] = mode
                assert mode in {"denied", "noop", "partial"}
                old_write = recovery._write_cgroup_file_reclaim
                writes = []
                def injected(size):
                    writes.append(size)
                    if mode == "denied":
                        raise PermissionError(errno.EPERM, "CI_FAULT_INJECTED")
                    if mode == "partial":
                        ballast.drop_cache(2 * MIB)
                        raise BlockingIOError(errno.EAGAIN, "CI_PARTIAL_RECLAIM")
                    # noop returns normally but releases no controlled bytes.
                recovery._write_cgroup_file_reclaim = injected
                recovery._LAST_ACTIVITY = time.monotonic() - 600
                recovery._LAST_ATTEMPT = 0.0
                recovery._START_MONOTONIC = time.monotonic() - 300_000
                try:
                    result["recovery"] = app._idle_memory_recovery_cycle()
                finally:
                    recovery._write_cgroup_file_reclaim = old_write
                result["write_requests"] = writes
                result["snapshot"] = cgroup_snapshot()
                result["scheduled"] = app.schedule_manual_collection("shopping")
                result["lifecycle_events"] = len(events)
            elif op == "hold":
                result["snapshot"] = cgroup_snapshot()
                result["scheduled"] = app.schedule_manual_collection(command.get("source", "shopping"))
                result["lifecycle_events"] = len(events)
            elif op == "lower_file_pressure":
                result["before"] = cgroup_snapshot()
                result["snapshot"] = ballast.lower_file_pressure(int(command.get("target_bytes", 140 * MIB)))
                result["base_anon_bytes"] = sum(map(len, ballast.base))
            elif op == "raise_file_pressure":
                result["snapshot"] = ballast.raise_file_pressure(int(command["target_bytes"]))
            elif op == "stabilize":
                result["before"] = cgroup_snapshot()
                result["snapshot"] = ballast.stabilize_cache(int(command["target_bytes"]))
            elif op == "release":
                result["base_anon_bytes_before"] = sum(map(len, ballast.base))
                result["released_pressure_bytes"] = sum(map(len, ballast.pressure))
                result["snapshot"] = ballast.release()
                result["base_anon_bytes_after"] = sum(map(len, ballast.base))
            elif op == "cycle":
                assert cycle_thread is None or not cycle_thread.is_alive(), "OVERLAPPING_CI_CYCLES"
                assert not app._isolated_heavy_worker_status()["slot_reserved"]
                clock.offset = float(command["clock_seconds"])
                os.environ["G2B_4303_PHASE"] = command["phase"]
                cycle_result = None
                cycle_thread = threading.Thread(target=run_cycle, daemon=True, name="ci-real-scheduler-cycle")
                cycle_thread.start()
                result["slot"] = original_slot(clock.wall())
            elif op == "status":
                result.update(cycle_running=bool(cycle_thread and cycle_thread.is_alive()),
                    cycle_result=cycle_result, worker=app._isolated_heavy_worker_status(),
                    state=dict(app._RECENT_COLLECTION_STATE), events=list(events), snapshot=cgroup_snapshot(),
                    budget_gate=app._DYNAMIC_SOURCE_NO_PROGRESS.status("budget"))
            elif op == "advance_clock":
                clock.offset = float(command["clock_seconds"])
                result["shopping_due"] = app._shopping_auto_due()
                completed = float(app._RECENT_COLLECTION_STATE["shopping_complete_monotonic"])
                result["due_before_four_hours"] = app._shopping_auto_due(now=completed + 14399)
                result["due_at_four_hours"] = app._shopping_auto_due(now=completed + 14400)
                result["slot"] = original_slot(clock.wall())
            else:
                raise AssertionError("UNKNOWN_CI_COMMAND")
            write_json(CONTROL / "reply.json", result)
    except BaseException as exc:
        write_json(CONTROL / "controller-error.json", {"error": repr(exc)})
        raise


def main():
    assert authorized(), "CI_PARENT_AUTH_HOLD"
    assert os.environ["G2B_RUNTIME_ROLE"] == "UNIFIED"
    os.environ["G2B_4303_SNAPSHOT_DATE"] = dt.datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    os.environ["G2B_4303_PHASE"] = "first"
    # Only the child gets sitecustomize through this explicit in-process value.
    os.environ["PYTHONPATH"] = "/app/scripts/g2b_4303_ci_hooks:/app/scripts:/app"
    import vnext_clean_app as app
    threading.Thread(target=controller, args=(app,), daemon=True, name="ci-parent-control").start()
    runpy.run_path(str(ROOT / "run.py"), run_name="__main__")


if __name__ == "__main__":
    main()
