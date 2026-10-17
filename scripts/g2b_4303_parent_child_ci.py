"""Fixtures installed only in the real parent-spawned g2b_heavy_worker child.

The original worker main, startup checks, backend, process lock, source lease,
HTTP clients, bounded parsers, attestation, digest, and PG transactions execute.
Only destination I/O (a local HTTP fixture) and collection scope are substituted.
"""
from __future__ import annotations

import atexit
import builtins
import datetime as dt
import json
import os
from pathlib import Path
import resource
import sys
import time
import urllib.parse
import urllib.request

from g2b_4303_parent_fixture_ci import (CONTROL, SCENARIOS, authorized,
    cgroup_snapshot, write_json, scenario_shape, scenario_total)


class Evidence:
    def __init__(self, mode, phase):
        self.mode, self.phase = mode, phase
        self.scenario = os.environ["G2B_4303_SCENARIO"]
        self.page_size, self.total_pages = scenario_shape(self.scenario, mode)
        self.total_rows = scenario_total(self.scenario, mode)
        self.pid, self.ppid = os.getpid(), os.getppid()
        self.cgroup = Path("/proc/self/cgroup").read_text()
        self.calls, self.stages = [], []
        self.read_bytes = self.read_calls = self.max_read_request_bytes = 0
        self.hash_calls = self.parsed_pages = self.preserved_rows = 0
        self.preserved_rows_by_page = {}
        self.pressure_applied = False
        self.status, self.error = "FAILED", ""
        self.checkpoint = None
        self.receipts = []
        self.item_count = self.state_count = 0
        self.classification = None

    def sample(self, stage):
        if len(self.stages) < 80:
            self.stages.append({"stage": stage, "time_ns": time.time_ns(),
                "rss_hwm_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "cgroup": cgroup_snapshot()})

    def pressure(self):
        assert not self.pressure_applied
        self.pressure_applied = True
        self.sample("before_transaction_pressure")
        write_json(CONTROL / "pressure-request.json", {"pid": self.pid, "target": 205_000_000})
        deadline = time.monotonic() + 20
        response = CONTROL / "pressure-ready.json"
        while not response.exists():
            assert time.monotonic() < deadline, "CI_PRESSURE_CONTROLLER_TIMEOUT"
            time.sleep(.01)
        self.pressure_snapshot = json.loads(response.read_text())
        response.unlink()
        self.sample("after_transaction_pressure")

    def finish(self):
        self.sample("worker_atexit")
        write_json(CONTROL / f"result-{self.mode}-{self.phase}.json", {
            **vars(self), "external_source_api_calls": 0,
            "fixture_transport_calls": len(self.calls),
            "rss_hwm_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})


class MeasuredResponse:
    def __init__(self, response, evidence):
        self.response, self.evidence = response, evidence

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.response.close()

    def read(self, size):
        from source_response_parser import CHUNK_BYTES
        assert 0 < size <= CHUNK_BYTES, "UNBOUNDED_TRANSPORT_READ"
        ev = self.evidence
        take = min(size, (8191, 127, 4093)[ev.read_calls % 3])
        data = self.response.read(take)
        ev.read_calls += 1
        ev.read_bytes += len(data)
        ev.max_read_request_bytes = max(ev.max_read_request_bytes, size)
        return data


def should_inject_rollback(evidence):
    return (evidence.scenario == "rollback" and evidence.phase != "rollback_resume"
        and evidence.calls[-1] == 3 and evidence.preserved_rows_by_page.get("3") == 2)


def install_fixture(app, ev):
    import budget_pg_collection as collector
    import budget_pg_store as store
    import budget_vnext
    import g2b_database
    import g2b_dynamic_source_budget as dynamic
    import lofin_vnext_http as budget_http
    import memory_guard
    import shopping_vnext
    import vnext_http as shopping_http
    import vnext_source_guard as guard
    from sqlalchemy import and_, func, select

    assert app._SOURCE_ONLY_RUNTIME, "CI_REQUIRES_REAL_SOURCE_ONLY_IMPORT"
    assert os.environ["G2B_SOURCE_DYNAMIC_CHILD"] == "1"
    ev.sample("after_source_import")
    budget_day = os.environ["G2B_4303_SNAPSHOT_DATE"]
    shopping_day = (dt.date.fromisoformat(budget_day) - dt.timedelta(days=1)).isoformat()
    scope = "ci4303:" + ev.scenario
    fmt = "xml" if "xml" in ev.scenario or ev.scenario == "rollback" else "json"
    budget_http.get_lofin_key = lambda: "CI_FIXTURE_NOT_A_CREDENTIAL"
    shopping_vnext._service_key = lambda: "CI_FIXTURE_NOT_A_CREDENTIAL"
    original_urlopen = urllib.request.urlopen

    def local_urlopen(request, timeout=0):
        parsed = urllib.parse.urlparse(request.full_url if hasattr(request, "full_url") else request)
        query = urllib.parse.parse_qs(parsed.query)
        if ev.mode == "budget":
            assert parsed.scheme == "https" and parsed.hostname == "www.lofin365.go.kr"
            assert parsed.path == "/lf/hub/QWGJK", "UNEXPECTED_SOURCE_DESTINATION"
            page, size = int(query["pIndex"][0]), int(query["pSize"][0])
        else:
            assert parsed.scheme == "https" and parsed.hostname == "apis.data.go.kr"
            assert parsed.path.endswith("/getDlvrReqDtlInfoList"), "UNEXPECTED_SOURCE_DESTINATION"
            page, size = int(query["pageNo"][0]), int(query["numOfRows"][0])
        assert size == ev.page_size and 1 <= page <= ev.total_pages
        ev.calls.append(page)
        ev.sample(f"before_http_read:{page}")
        # Real loopback HTTP socket; fixture server and telemetry are outside
        # the tested cgroup. No credential or original URL leaves this process.
        local = f"http://127.0.0.1:8037/{ev.mode}/page-{page}.{fmt}"
        return MeasuredResponse(original_urlopen(local, timeout=10), ev)
    urllib.request.urlopen = local_urlopen

    transport = budget_http if ev.mode == "budget" else shopping_http
    original_parse = transport.parse_response
    def measured_parse(*args, **kwargs):
        ev.sample("before_parse")
        result = original_parse(*args, **kwargs)
        assert len(result[0]) == min(ev.page_size, ev.total_rows - (ev.calls[-1] - 1) * ev.page_size)
        ev.parsed_pages += 1
        ev.sample("after_parse")
        return result
    transport.parse_response = measured_parse
    original_digest = guard.source_transport_result_sha256
    def measured_digest(*args, **kwargs):
        result = original_digest(*args, **kwargs)
        ev.hash_calls += 1
        ev.sample("transport_digest:" + str(ev.hash_calls))
        return result
    guard.source_transport_result_sha256 = measured_digest

    original_preserve = store.preserve_observation
    def preserve(*args, **kwargs):
        result = original_preserve(*args, **kwargs)
        ev.preserved_rows += 1
        page = str(ev.calls[-1])
        ev.preserved_rows_by_page[page] = ev.preserved_rows_by_page.get(page, 0) + 1
        if should_inject_rollback(ev):
            ev.pressure()
            dynamic.allocation_checkpoint(reserve_bytes=16 * (1 << 20))
            raise AssertionError("TRANSACTION_PRESSURE_DID_NOT_HOLD")
        if ev.scenario == "no_progress" and ev.preserved_rows == 2:
            # Simulate the next large allocation, using the real synchronous
            # guard and transaction rollback. No fake cgroup sample or OOM.
            dynamic.allocation_checkpoint(reserve_bytes=64 * (1 << 20))
            raise AssertionError("NO_PROGRESS_ALLOCATION_WAS_NOT_HELD")
        return result
    store.preserve_observation = preserve

    def inspect_budget():
        cp = store.get_checkpoint("budget", scope)
        if cp is None:
            return
        ev.checkpoint = {key: cp[key] for key in ("page_no", "page_size", "status",
            "fetched_count", "saved_count", "source_total", "last_error", "cursor_value")}
        ev.checkpoint["verified"] = collector.verified_checkpoint(cp)
        generation = json.loads(cp["cursor_value"])["generation"]
        engine, tables = store._engine_and_tables()
        with engine.connect() as conn:
            selected = and_(tables["pages"].c.dataset == "budget",
                tables["pages"].c.scope_key == scope, tables["pages"].c.generation == generation)
            ev.receipts = list(conn.execute(select(tables["pages"].c.page_no).where(selected)
                .order_by(tables["pages"].c.page_no)).scalars())
            selected_items = and_(tables["items"].c.dataset == "budget",
                tables["items"].c.scope_key == scope, tables["items"].c.generation == generation)
            ev.item_count = conn.execute(select(func.count()).select_from(tables["items"])
                .where(selected_items)).scalar_one()
            ev.state_count = conn.execute(select(func.count()).select_from(tables["states"])
                .where(tables["states"].c.record_key.like("CI4303-B-%"))).scalar_one()
        assert ev.item_count == ev.state_count == int(cp["saved_count"]), "UNCOMMITTED_ROW_LEAK"
        assert ev.receipts == list(range(1, int(cp["page_no"]))), "RECEIPT_SKIP_OR_DUPLICATE"
        assert ev.checkpoint["verified"], "UNVERIFIED_CHECKPOINT"

    def inspect_shopping():
        import shopping_store_v41
        import vnext_collection
        import vnext_store
        cp = vnext_store.get_checkpoint("shopping_delivery", f"{shopping_day}:{shopping_day}")
        if cp is None:
            return
        ev.checkpoint = {key: cp[key] for key in ("page_no", "page_size", "status",
            "fetched_count", "saved_count", "source_total", "last_error", "cursor_value")}
        ev.checkpoint["verified"] = vnext_collection.verified_compact_completion(cp)
        ev.state_count = shopping_store_v41.count()["records"]
        assert ev.state_count == 999 and ev.checkpoint["verified"], ev.checkpoint

    def classify_real_backlog():
        import classification_vnext as classifier
        import db
        ev.sample("before_real_pg_classification")
        original_primary = store.save_classification
        original_compat = classifier.save_compat_classification
        pending = []
        shared_transactions = 0
        def primary(*args, **kwargs):
            result = original_primary(*args, **kwargs)
            connection = kwargs["_conn"]
            assert connection.in_transaction()
            assert not pending
            pending.append((connection, connection.get_transaction()))
            return result
        def compatible(*args, **kwargs):
            nonlocal shared_transactions
            connection = kwargs["_conn"]._conn
            primary_connection, transaction = pending.pop()
            assert connection is primary_connection and connection.get_transaction() is transaction
            result = original_compat(*args, **kwargs)
            shared_transactions += 1
            return result
        store.save_classification = primary
        classifier.save_compat_classification = compatible
        try:
            result = classifier.classify_dataset("budget", batch_size=500, max_batches=32)
        finally:
            store.save_classification = original_primary
            classifier.save_compat_classification = original_compat
        expected = ev.total_rows
        assert result["classified"] == expected and result["physical_batch_size"] == 1, result
        assert result["batch_size"] == 500 and result["max_batches"] == 32, result
        assert result["physical_batches_done"] == expected and shared_transactions == expected
        primary_rows = store.classification_rows(["budget"], result["classifier_version"])
        with db.connect() as connection:
            compatible_rows = connection.execute(
                "SELECT entity_key,source_payload_sha256,primary_category FROM classifications "
                "WHERE entity_type=? AND classifier_version=?",
                ("budget", result["classifier_version"])).fetchall()
        compatible_rows = {row["entity_key"]: row for row in compatible_rows}
        assert len(primary_rows) == len(compatible_rows) == expected
        for row in primary_rows:
            saved = compatible_rows[row["record_key"]]
            assert row["source_payload_sha256"] == saved["source_payload_sha256"]
            assert row["primary_category"] == saved["primary_category"] == "LIGHTING"
        ev.classification = {"result": result, "shared_pg_transactions": shared_transactions,
            "exact_primary_compat_hash_matches": expected}
        ev.sample("after_real_pg_classification")

    def run_fixture(*, source):
        assert source == ev.mode
        ev.sample("backend_ready_before_source_lease")
        ev.database_dialect = g2b_database.engine().dialect.name
        assert ev.database_dialect == "postgresql"
        try:
            with g2b_database.operational_source_cycle_lease(source) as acquired:
                assert acquired, "CI_SOURCE_LEASE_NOT_ACQUIRED"
                if source == "budget":
                    with guard.operational_budget_source_context(snapshot_date=budget_day, max_requests=32):
                        result = collector.collect_pages(dataset="budget", scope=scope,
                            range_start=budget_day[:4], range_end=budget_day,
                            page_size=ev.page_size, max_pages=ev.total_pages, resume=True,
                            fetch=lambda page, size: budget_vnext.fetch_page(int(budget_day[:4]), budget_day, page, size),
                            identity=lambda row: row["dbiz_cd"], source_system="CI_HTTP_FIXTURE",
                            source_operation="QWGJK", source_date=lambda row: budget_day,
                            checkpoint_contract="G2B_PARENT_4303_HTTP_TRANSACTION_V1")
                        ev.transport_context = guard.current_source_request_context()
                else:
                    with guard.operational_recent_source_context(collection_date=shopping_day, max_requests=4):
                        result = shopping_vnext.collect_all(shopping_day, shopping_day,
                            page_size=999, max_pages=1, resume=True)
                        ev.transport_context = guard.current_source_request_context()
                ev.status = "COMPLETE" if result["complete"] else "PARTIAL"
                if source == "budget" and ev.scenario == "json" and result["complete"]:
                    classify_real_backlog()
        except memory_guard.MemoryPressureError as exc:
            ev.status, ev.error = "WAITING_MEMORY", str(exc)
            raise
        finally:
            (inspect_budget if source == "budget" else inspect_shopping)()
            ev.sample("checkpoint_verified_after_transaction")
    app._run_recent_collection_once = run_fixture
    app.recent_collection_status = lambda: {f"{ev.mode}_run_state": ev.status,
                                           f"{ev.mode}_last_error": ev.error}


def install_child_hook():
    assert authorized(), "CI_CHILD_AUTH_HOLD"
    mode = sys.argv[-1]
    assert mode in {"shopping", "budget"}
    phase = os.environ["G2B_4303_PHASE"]
    assert phase in {"first", "resume", "resume2", "rollback_resume", "recheck"}
    ev = Evidence(mode, phase)
    ev.sample("real_heavy_worker_preimport")
    atexit.register(ev.finish)
    real_import = builtins.__import__
    installed = False
    def instrument(name, globals=None, locals=None, fromlist=(), level=0):
        nonlocal installed
        module = real_import(name, globals, locals, fromlist, level)
        if name == "vnext_clean_app" and not installed:
            installed = True
            builtins.__import__ = real_import
            install_fixture(module, ev)
        return module
    builtins.__import__ = instrument
