"""Host-side orchestration and independent kernel/HTTP evidence for PR359.

Docker and disposable PostgreSQL are required. A skipped/unsupported local run
is never reported as a memory pass. Every scenario owns a fresh 256MiB cgroup;
PG, the loopback source fixture, HTTP probes and all sampling remain outside it.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from g2b_4303_parent_fixture_ci import (CI_DATABASE, MIB, PAGE_SIZE, PAGES, TOTAL_ROWS,
    SCENARIOS, baseline_contract, write_json)

PORT = 8034
LIMIT_BYTES = 268435456
HIGH_BYTES = 220_000_000
COVERAGE_BOUNDARIES = [
    "Disposable CI only: no deployment, production database, or external source API request.",
    "Reclaim permission-denied/no-op/partial syscalls are fault-injected; every admission uses fresh real kernel samples.",
    "Successful recovery removes only the private controlled clean-file cache; no host drop_caches, cgroup reset, or anonymous baseline release.",
    "An accelerated app clock drives actual shopping/budget scheduler cycles; wall-clock four-hour soak is not claimed.",
    "Fixture scopes replace the full multi-day orchestration inside the source child; parent orchestration and transaction paths remain real.",
    "Bounded public HTML and live/health/ready are exercised; HTML probes pause only during deliberate idle-reclaim windows. Authenticated exports and arbitrary unrelated web allocations are not covered.",
    "Near-cap JSON/XML demonstrate safe HOLD only, not successful near-cap collection. Full multi-day and precollection source orchestration are excluded.",
]


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def counters(path):
    return {key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())}


def check_kernel_evidence(before, after, peak):
    for event in ("max", "oom", "oom_kill"):
        assert event in before and event in after, "INCOMPLETE_KERNEL_EVENTS"
        assert before[event] == after[event] == 0, (event, before, after)
    assert 0 < peak < HIGH_BYTES, ("STRICT_DECIMAL_PEAK_TARGET_FAILED", peak)


def check_budget_checkpoint(worker, next_page, *, status):
    cp = worker["checkpoint"]
    size = worker["page_size"]
    assert cp and cp["verified"], worker
    assert cp["page_no"] == next_page and cp["page_size"] == size, cp
    assert cp["fetched_count"] == cp["saved_count"] == min((next_page - 1) * size, worker.get("total_rows", (next_page - 1) * size)), cp
    assert worker["item_count"] == worker["state_count"] == cp["saved_count"], worker
    assert worker["receipts"] == list(range(1, next_page)), worker
    assert cp["status"] == status, cp
    return json.loads(cp["cursor_value"])["generation"]


def validate_lifecycle(events, parent_pid):
    active = None
    spawned = []
    for event in events:
        assert event["parent_pid"] == parent_pid
        if event["event"] == "spawned":
            assert active is None, ("CHILD_OVERLAP", events)
            active = event["child_pid"]
            spawned.append(event)
        elif event["event"] == "reaped":
            assert active == event["child_pid"], ("REAP_ORDER", events)
            active = None
    assert active is None, "UNREAPED_FINAL_SLOT"
    return spawned


class Sampler(threading.Thread):
    def __init__(self, cgroup, output):
        super().__init__(daemon=True)
        self.cgroup, self.output = cgroup, output
        self.stop_event = threading.Event()
        self.error = None
        self.live_latencies, self.html_latencies = [], []
        self.samples = 0
        self.web_pids = set()
        self.worker_pids = set()
        self.active = False
        self.html_enabled = True

    def probe(self, route):
        start = time.monotonic()
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{route}", timeout=3) as response:
            assert response.status == 200
            body = response.read(1024 * 1024 + 1)
            assert len(body) <= 1024 * 1024, "UNBOUNDED_WEB_RESPONSE"
            if route == "/login":
                assert b"<html" in body.lower() or b"<!doctype html" in body.lower(), "PUBLIC_HTML_NOT_RENDERED"
            if route == "/live":
                data = json.loads(body)
                assert data["process_alive"] is True and not data.get("recovery_mode"), data
                assert "BOOTSTRAP" not in str(data.get("runtime", "")), data
        elapsed = time.monotonic() - start
        if route == "/live":
            self.live_latencies.append(elapsed)
        elif route == "/login":
            self.html_latencies.append(elapsed)
        assert elapsed < 3, (route, elapsed)

    def run(self):
        next_live = next_html = 0.0
        try:
            with self.output.open("w") as stream:
                while not self.stop_event.is_set():
                    now = time.monotonic()
                    children = []
                    for pid in (self.cgroup / "cgroup.procs").read_text().split():
                        try:
                            command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
                        except (FileNotFoundError, ProcessLookupError):
                            continue
                        assert "postgres" not in command, "DATABASE_MUST_BE_OUTSIDE_TEST_CGROUP"
                        if "-m g2b_heavy_worker " in command:
                            children.append(int(pid))
                            self.worker_pids.add(int(pid))
                        if "g2b_4303_parent_web_ci.py" in command:
                            self.web_pids.add(int(pid))
                    assert len(children) <= 1, ("OVERLAPPING_SOURCE_CHILDREN", children)
                    events = counters(self.cgroup / "memory.events")
                    peak = int((self.cgroup / "memory.peak").read_text())
                    check_kernel_evidence(events, events, peak)
                    stream.write(json.dumps({"time_ns": time.time_ns(),
                        "current_bytes": int((self.cgroup / "memory.current").read_text()),
                        "peak_bytes": peak, "events": events, "source_pids": children}) + "\n")
                    self.samples += 1
                    if self.active and now >= next_live:
                        self.probe("/live")
                        next_live = now + .2
                    if self.active and self.html_enabled and now >= next_html:
                        self.probe("/health")
                        self.probe("/login")
                        self.probe("/ready")
                        next_html = now + 1
                    self.stop_event.wait(.025)
        except BaseException as exc:
            self.error = repr(exc)
            self.stop_event.set()

    def finish(self, *, expect_workers=True):
        self.stop_event.set()
        self.join(timeout=10)
        assert not self.is_alive() and self.error is None, self.error
        assert self.samples > 10 and self.live_latencies and self.html_latencies
        assert len(self.web_pids) == 1, ("WEB_PID_CHANGED", self.web_pids)
        assert bool(self.worker_pids) is bool(expect_workers), ("UNEXPECTED_WORKER_OBSERVATION", self.worker_pids)
        return {"samples": self.samples, "web_host_pid": next(iter(self.web_pids)),
            "worker_host_pids": sorted(self.worker_pids), "live_max_seconds": max(self.live_latencies),
            "html_max_seconds": max(self.html_latencies), "live_probes": len(self.live_latencies),
            "html_probes": len(self.html_latencies)}


class FixtureHTTP:
    def __init__(self, root):
        # Host sees only this initially empty, private disk mount. The web
        # creates/fsyncs all files after entering its own cgroup.
        root = Path(root)
        self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parts = self.path.split("/")
                if len(parts) != 3 or parts[1] not in {"shopping", "budget"} or not parts[2].startswith("page-") or not parts[2].endswith((".json", ".xml")):
                    self.send_error(404)
                    return
                path = root / parts[1] / parts[2]
                if not path.is_file():
                    self.send_error(404)
                    return
                owner.requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("Content-Type", "application/json" if path.suffix == ".json" else "application/xml")
                self.end_headers()
                try:
                    with path.open("rb") as stream:
                        while chunk := stream.read(8192):
                            self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # A genuine bounded read HOLD may close early.
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 8037), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def wait_file(path, *, timeout=180, sampler=None):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if sampler:
            assert sampler.error is None, sampler.error
        failure = path.parent / "controller-error.json"
        assert not failure.exists(), failure.read_text() if failure.exists() else ""
        assert time.monotonic() < deadline, ("CI_TIMEOUT", str(path))
        time.sleep(.05)
    return json.loads(path.read_text())


def command(directory, op, sampler=None, **kwargs):
    reply = directory / "reply.json"
    assert not reply.exists(), "UNCONSUMED_PARENT_REPLY"
    write_json(directory / "command.json", {"op": op, **kwargs})
    result = wait_file(reply, sampler=sampler)
    reply.unlink()
    assert result["op"] == op
    return result


def wait_ready(sampler):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        assert sampler.error is None, sampler.error
        try:
            for route in ("/ready", "/live", "/health", "/login"):
                sampler.probe(route)
            sampler.active = True
            return
        except Exception:
            time.sleep(.25)
    raise AssertionError("REAL_WEB_NOT_READY")


def cycle(directory, sampler, seconds, phase):
    command(directory, "cycle", sampler, clock_seconds=seconds, phase=phase)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        state = command(directory, "status", sampler)
        if not state["cycle_running"]:
            assert state["cycle_result"] is not None and "error" not in state["cycle_result"], state
            assert not state["worker"]["slot_reserved"] and not state["worker"]["supervisor_running"], state
            return state
        time.sleep(.1)
    raise AssertionError("REAL_PARENT_CYCLE_TIMEOUT")


def no_progress_lifetime(directory, sampler, parent, previous):
    attempts = []
    generation = None
    if previous is None:
        first_baseline = None
        for phase in ("first", "resume"):
            if first_baseline is not None:
                stable = command(directory, "stabilize", sampler, target_bytes=first_baseline)
                assert abs(stable["snapshot"]["current_bytes"] - first_baseline) <= 2 * MIB
            result = cycle(directory, sampler, 7200, phase)
            assert result["cycle_result"]["scheduled"] == {"shopping": False, "budget": True}, result
            worker = wait_file(directory / f"result-budget-{phase}.json", sampler=sampler)
            assert worker["status"] == "WAITING_MEMORY" and worker["calls"] == [1], worker
            current_generation = check_budget_checkpoint(worker, 1, status="INCOMPLETE")
            assert generation is None or generation == current_generation
            generation = current_generation
            assert worker["ppid"] == parent["web_pid"] and worker["cgroup"] == parent["cgroup"]
            assert worker["hash_calls"] == 2 and worker["parsed_pages"] == 1
            launches = [event for event in result["events"] if event["event"] == "launch_attempt"]
            if first_baseline is None:
                first_baseline = launches[0]["snapshot"]["current_bytes"]
            assert abs(launches[-1]["snapshot"]["current_bytes"] - first_baseline) <= 2 * MIB, launches
            attempts.append(result)
        last = attempts[-1]
        assert last["budget_gate"]["consecutive_no_progress"] == 2, last
        blocked = last["budget_gate"]["blocked_baseline_bytes"]
        assert blocked > 0 and blocked + 52 * MIB < HIGH_BYTES, last
        assert not last["budget_gate"]["active_generation"], last
        assert len(validate_lifecycle(last["events"], parent["web_pid"])) == 2
        event_count = len(last["events"])
    else:
        blocked = previous["blocked_baseline_bytes"]
        status = command(directory, "status", sampler)
        assert status["budget_gate"]["consecutive_no_progress"] == 2, status
        assert status["budget_gate"]["blocked_baseline_bytes"] == blocked, status
        assert not status["budget_gate"]["active_generation"], status
        assert abs(status["snapshot"]["current_bytes"] - blocked) <= 2 * MIB, status
        assert status["snapshot"]["current_bytes"] + 52 * MIB < HIGH_BYTES, status
        event_count = 0
    # Exercise both production automatic scheduling and ordinary manual
    # refresh. Neither is an explicit operator override of the durable latch.
    automatic = cycle(directory, sampler, 7200, "recheck")
    assert automatic["cycle_result"]["scheduled"] == {"shopping": False, "budget": False}, automatic
    manual = command(directory, "hold", sampler, source="budget")
    assert not manual["scheduled"] and manual["lifecycle_events"] == event_count, manual
    final = command(directory, "status", sampler)
    assert len(final["events"]) == event_count and not final["worker"]["slot_reserved"], final
    assert not (directory / "result-budget-recheck.json").exists(), "HELD_GATE_SPAWNED_CHILD"
    return {"blocked_baseline_bytes": blocked, "attempts": attempts,
        "automatic_held": True, "ordinary_manual_held": True, "lifecycle": final["events"],
        "gate": final["budget_gate"], "same_database_preserved_across_restart": previous is not None}


def scenario(name, directory, *, restart_evidence=None):
    directory.mkdir(parents=True)
    disk = directory / "disk"
    disk.mkdir()
    assert not list(disk.iterdir()), "HOST_PREWARMED_DISK_FIXTURE_FORBIDDEN"
    container = f"g2b-parent-4303-{name}-{os.getpid()}"
    args = ["docker", "run", "-d", "--name", container, "--memory=256m", "--memory-swap=256m",
        "--pids-limit=96", "--network", "host", "--security-opt=no-new-privileges",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=24m",
        "--mount", f"type=bind,source={ROOT},target=/app,readonly",
        "--mount", f"type=bind,source={directory},target=/ci-control",
        "--mount", f"type=bind,source={disk},target=/var/tmp/g2b-parent-4303-ci", "--workdir", "/app"]
    env = {"G2B_DATABASE_URL": CI_DATABASE, "G2B_CI_PARENT_4303": "1", "G2B_4303_SCENARIO": name,
        "G2B_TEST_MODE": "0", "G2B_RUNTIME_ROLE": "UNIFIED", "G2B_AUTO_SYNC_DISABLE": "1",
        "G2B_AUTO_SYNC": "0", "G2B_POST_BOOT_MAINTENANCE_ENABLE": "0", "G2B_MATCH_ROLLOVER_AUTO_ENABLE": "0",
        "G2B_V41_FRESH_START": "0", "G2B_DESTRUCTIVE_RESET_CONFIRM": "0",
        "G2B_256MIB_DYNAMIC_SOURCE_BUDGET": "1", "G2B_IDLE_CGROUP_FILE_RECLAIM": "1",
        "G2B_MEMORY_SOFT_LIMIT_MB": "160", "G2B_ISOLATED_WORKER_SOFT_LIMIT_MB": "112",
        "G2B_APP_SCHEMA": f"g2b_app_parent_4303_{name}", "G2B_BUDGET_SCHEMA": f"g2b_budget_parent_4303_{name}",
        "G2B_SERVICE_KEY": "", "LOFIN_API_KEY": "", "EDUINFO_API_KEY": "", "PORT": str(PORT)}
    for key, value in env.items():
        args.extend(["-e", f"{key}={value}"])
    args.extend(["g2b-4-1-303-ci", "python", "-B", "scripts/g2b_4303_parent_web_ci.py"])
    sampler = server = None
    try:
        run(*args)
        pid = int(run("docker", "inspect", "--format", "{{.State.Pid}}", container))
        cgline = next(line for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines() if line.startswith("0::"))
        assert cgline not in Path("/proc/self/cgroup").read_text().splitlines(), "TELEMETRY_MUST_BE_EXTERNAL"
        cgroup = Path("/sys/fs/cgroup") / cgline[3:].lstrip("/")
        assert int((cgroup / "memory.max").read_text()) == LIMIT_BYTES
        assert int((cgroup / "memory.swap.max").read_text()) == 0, "SWAP_MUST_BE_DISABLED"
        before = counters(cgroup / "memory.events")
        server = FixtureHTTP(disk)
        sampler = Sampler(cgroup, directory / "host-samples.jsonl")
        sampler.start()
        parent = wait_file(directory / "parent-ready.json", sampler=sampler)
        wait_ready(sampler)
        baseline = command(directory, "prepare", sampler)["snapshot"]
        baseline_contract(baseline)
        assert parent["startup_reserve_bytes"] == 52 * MIB
        assert not command(directory, "hold", sampler)["scheduled"], "173MIB_MUST_NOT_BYPASS_52MIB_RESERVE"
        sampler.html_enabled = False
        time.sleep(.5)
        reclaims = []
        for mode in ("denied", "noop", "partial"):
            result = command(directory, "reclaim", sampler, mode=mode)
            assert len(result["write_requests"]) == 1 and result["fault_mode"] == mode, result
            cache_result = result["recovery"]["cgroup_file_reclaim"]
            assert cache_result["requested_mib"] >= 8, result
            if mode == "denied":
                assert cache_result["error_code"] == "PermissionError", result
            elif mode == "partial":
                assert cache_result["error_code"] == "BlockingIOError", result
                assert 0 < cache_result["released_mib"] < cache_result["requested_mib"], result
            else:
                assert not cache_result["error_code"], result
            assert not result["scheduled"] and result["lifecycle_events"] == 0, result
            assert result["snapshot"]["current_bytes"] + 52 * MIB >= HIGH_BYTES, result
            reclaims.append(result)
        sampler.html_enabled = True
        recovery = command(directory, "lower_file_pressure", sampler,
            target_bytes=(restart_evidence["blocked_baseline_bytes"] if restart_evidence else 140 * MIB))
        assert recovery["snapshot"]["current_bytes"] + 52 * MIB < HIGH_BYTES, recovery
        assert abs(recovery["snapshot"]["effective_bytes"] / MIB - 115) <= 2, recovery
        if name == "no_progress":
            evidence = no_progress_lifetime(directory, sampler, parent, restart_evidence)
            telemetry = sampler.finish(expect_workers=restart_evidence is None)
            sampler = None
            after = counters(cgroup / "memory.events")
            peak = int((cgroup / "memory.peak").read_text())
            check_kernel_evidence(before, after, peak)
            report = {"scenario": name, "status": "passed", "intentional_restart_lifetime":
                2 if restart_evidence else 1, "parent": parent, "web": telemetry,
                "baseline": baseline, "measured_file_recovery": recovery,
                "reclaim_faults": reclaims, "kernel_peak_bytes": peak,
                "memory_events_before": before, "memory_events_after": after,
                "fixture_http_requests": server.requests, "external_source_api_calls": 0,
                "coverage_boundaries": COVERAGE_BOUNDARIES, **evidence}
            assert len(server.requests) == (0 if restart_evidence else 2), server.requests
            write_json(directory / "acceptance.json", report)
            print("G2B_4303_REAL_PARENT_RESTART_GATE_PASS", report["intentional_restart_lifetime"],
                  "peak_bytes", peak, "fixture_requests", len(server.requests), flush=True)
            return report
        shopping = cycle(directory, sampler, 0, "first")
        assert shopping["cycle_result"]["scheduled"] == {"shopping": True, "budget": False}, shopping
        shop_worker = wait_file(directory / "result-shopping-first.json", sampler=sampler)
        assert shop_worker["status"] == "COMPLETE" and shop_worker["calls"] == [1], shop_worker
        assert shop_worker["state_count"] == 999 and shop_worker["checkpoint"]["verified"], shop_worker
        assert shop_worker["hash_calls"] == 2 and shop_worker["parsed_pages"] == 1
        assert shop_worker["read_bytes"] > 4 * MIB, "LARGE_SHOPPING_BODY_NOT_EXERCISED"
        if name.startswith("near_cap_"):
            bounded_pressure = command(directory, "raise_file_pressure", sampler, target_bytes=155 * MIB)
            assert bounded_pressure["snapshot"]["current_bytes"] + 52 * MIB < HIGH_BYTES
        budget = cycle(directory, sampler, 7200, "first")
        assert budget["cycle_result"]["scheduled"] == {"shopping": False, "budget": True}, budget
        worker = wait_file(directory / "result-budget-first.json", sampler=sampler)
        resumed = None
        if name == "rollback":
            assert worker["status"] == "WAITING_MEMORY" and worker["calls"] == [1, 2, 3], worker
            generation = check_budget_checkpoint(worker, 3, status="INCOMPLETE")
            # Two real parent attempts still hold under the retained pressure.
            for _ in range(2):
                held = command(directory, "hold", sampler, source="budget")
                assert not held["scheduled"], held
            command(directory, "release", sampler)
            resumed = cycle(directory, sampler, 7200, "resume")
            resumed_worker = wait_file(directory / "result-budget-resume.json", sampler=sampler)
            assert resumed_worker["status"] == "COMPLETE", resumed_worker
            assert check_budget_checkpoint(resumed_worker, PAGES + 1, status="COMPLETE") == generation
            assert resumed_worker["calls"] == [3], resumed_worker
            assert worker["calls"] + resumed_worker["calls"] == [1, 2, 3, 3]
        elif name.startswith("near_cap_"):
            # Legal near-16MiB bodies may be held before expansion. They must
            # not attest/commit a partial page or be mislabeled complete.
            assert worker["status"] == "WAITING_MEMORY", worker
            check_budget_checkpoint(worker, 1, status="INCOMPLETE")
            assert worker["calls"] == [1] and worker["hash_calls"] == 0, worker
            command(directory, "lower_file_pressure", sampler)
        else:
            assert worker["status"] == "COMPLETE", worker
            check_budget_checkpoint(worker, PAGES + 1, status="COMPLETE")
            assert worker["calls"] == list(range(1, PAGES + 1)), worker
            if name == "json":
                assert worker["classification"]["shared_pg_transactions"] == TOTAL_ROWS
                assert worker["classification"]["exact_primary_compat_hash_matches"] == TOTAL_ROWS
        for ev in (shop_worker, worker):
            assert ev["ppid"] == parent["web_pid"] and ev["cgroup"] == parent["cgroup"], ev
            assert ev["hash_calls"] == 2 * ev["parsed_pages"], ev
            assert ev["external_source_api_calls"] == 0 and ev["database_dialect"] == "postgresql"
            assert 0 < ev["max_read_request_bytes"] <= 16384
        # Four hours is a real clock input to production due/stagger logic.
        # A completed shopping checkpoint must cause zero transport on recheck.
        early = command(directory, "advance_clock", sampler, clock_seconds=14000)
        assert early["shopping_due"] is False, early
        assert early["due_before_four_hours"] is False and early["due_at_four_hours"] is True, early
        final = cycle(directory, sampler, 14430, "recheck")
        assert final["cycle_result"]["scheduled"] == {"shopping": True, "budget": False}, final
        recheck = wait_file(directory / "result-shopping-recheck.json", sampler=sampler)
        assert recheck["status"] == "COMPLETE" and recheck["calls"] == [], recheck
        launches = validate_lifecycle(final["events"], parent["web_pid"])
        expected = ["shopping", "budget", "budget", "shopping"] if name == "rollback" else ["shopping", "budget", "shopping"]
        assert [event["mode"] for event in launches] == expected, launches
        for result in (shopping, budget, resumed, final):
            if result:
                assert not result["worker"]["pending"], result
        sampler.probe("/live")
        sampler.probe("/login")
        telemetry = sampler.finish()
        sampler = None
        after = counters(cgroup / "memory.events")
        peak = int((cgroup / "memory.peak").read_text())
        check_kernel_evidence(before, after, peak)
        report = {"scenario": name, "status": "passed", "baseline": baseline,
            "reclaim_faults": reclaims, "measured_file_recovery": recovery, "parent": parent,
            "web": telemetry, "kernel_peak_bytes": peak, "memory_events_before": before,
            "memory_events_after": after, "lifecycle": final["events"],
            "fixture_http_requests": server.requests, "external_source_api_calls": 0,
            "coverage_boundaries": COVERAGE_BOUNDARIES}
        write_json(directory / "acceptance.json", report)
        print("G2B_4303_REAL_PARENT_CGROUP_PASS", name, "peak_bytes", peak,
              "spawn_reap_pairs", len(launches), "source_api_calls", 0, flush=True)
    except BaseException as exc:
        write_json(directory / "failure.json", {"scenario": name, "error": repr(exc)})
        raise
    finally:
        if sampler:
            sampler.stop_event.set()
            sampler.join(timeout=10)
        if server:
            server.close()
        try:
            log = run("docker", "logs", container)
            (directory / "container.log").write_text(log)
            if (directory / "failure.json").exists():
                print(log[-18000:], flush=True)
        finally:
            subprocess.run(["docker", "rm", "-f", container], check=False, stdout=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=SCENARIOS, action="append")
    args = parser.parse_args()
    assert os.environ.get("G2B_DATABASE_URL") == CI_DATABASE, "DISPOSABLE_LOCAL_DATABASE_ONLY"
    assert not any(os.environ.get(k) for k in ("G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY"))
    assert shutil.which("docker"), "DOCKER_UNAVAILABLE_REAL_CGROUP_NOT_RUN"
    artifact_root = ROOT / "verification" / "g2b-parent-4303"
    artifact_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=artifact_root))
    selected = args.scenario or list(SCENARIOS)
    summary = {"status": "running", "scenarios_requested": selected, "scenarios_passed": [],
        "kernel_limit_bytes": LIMIT_BYTES, "cooperative_high_bytes": HIGH_BYTES,
        "coverage_boundaries": COVERAGE_BOUNDARIES}
    print("G2B_4303_PARENT_EVIDENCE", directory, flush=True)
    try:
        for name in selected:
            if name == "no_progress":
                first = scenario(name, directory / name / "lifetime-1")
                second = scenario(name, directory / name / "lifetime-2", restart_evidence=first)
                assert first["web"]["web_host_pid"] != second["web"]["web_host_pid"], "REAL_WEB_RESTART_NOT_OBSERVED"
                write_json(directory / name / "restart-acceptance.json", {
                    "status": "passed", "lifetime_1_peak": first["kernel_peak_bytes"],
                    "lifetime_2_peak": second["kernel_peak_bytes"],
                    "old_web_host_pid": first["web"]["web_host_pid"],
                    "new_web_host_pid": second["web"]["web_host_pid"],
                    "durable_gate_preserved": True, "post_restart_fixture_calls": 0,
                    "post_restart_popen_count": 0})
            else:
                scenario(name, directory / name)
            summary["scenarios_passed"].append(name)
    except BaseException as exc:
        summary.update(status="failed", error=repr(exc))
        raise
    else:
        summary["status"] = "passed"
    finally:
        write_json(directory / "summary.json", summary)


if __name__ == "__main__":
    main()
