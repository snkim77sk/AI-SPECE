"""Disposable PostgreSQL regional-reader canary; never a production command.

Runs only in GitHub Actions with the exact ephemeral database/schema below.
The 256 MiB cgroup contains this reader, not PostgreSQL, the web or a worker.
No collector, source API, migration repair, or production data is exercised.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CI_DATABASE = "postgresql://postgres:postgres@127.0.0.1:5432/g2b_region_reads_4304_ci"
CI_SCHEMA = "g2b_budget_4304_ci"
MIB = 1 << 20
CGROUP = Path("/sys/fs/cgroup")
LAYERS = ("DETAIL_EXECUTION", "APPROPRIATION", "EDUCATION")
REGIONS = (
    ("충청남도", "충남", "천안시"),
    ("충청북도", "충북", "청주시"),
    ("경상남도", "경남", "창원시"),
    ("경상북도", "경북", "포항시"),
)
SOURCE_KEYS = ("G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY")
ALTERNATE_DATABASE_KEYS = (
    "G2B_BUDGET_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL",
    "DB_HOST", "DB_NAME", "DB_DATABASE", "PGHOST", "PGDATABASE", "POSTGRES_HOST",
)


def authorized(environ=None):
    """Pure, fail-closed guard; checked before any database import or I/O."""
    env = os.environ if environ is None else environ
    return bool(
        env.get("GITHUB_ACTIONS") == "true"
        and env.get("G2B_CI_REGION_READ_4304") == "1"
        and env.get("G2B_DATABASE_URL") == CI_DATABASE
        and env.get("G2B_BUDGET_SCHEMA") == CI_SCHEMA
        and env.get("G2B_APP_SCHEMA") == "g2b_app_4304_ci"
        and env.get("G2B_TEST_MODE") == "0"
        and env.get("G2B_BUDGET_STORAGE") == "postgresql"
        and env.get("G2B_AUTO_SYNC_DISABLE") == "1"
        and not any(env.get(key) for key in SOURCE_KEYS + ALTERNATE_DATABASE_KEYS)
    )


def memory_snapshot():
    status = Path("/proc/self/status").read_text()
    rss = int(re.search(r"^VmHWM:\s+(\d+) kB", status, re.M)[1]) * 1024
    events = dict(line.split() for line in (CGROUP / "memory.events").read_text().splitlines())
    return {
        "rss_peak_bytes": rss,
        "cgroup_current_bytes": int((CGROUP / "memory.current").read_text()),
        "cgroup_peak_bytes": int((CGROUP / "memory.peak").read_text()),
        "cgroup_limit_bytes": int((CGROUP / "memory.max").read_text()),
        "swap_limit_bytes": int((CGROUP / "memory.swap.max").read_text()),
        "oom": int(events.get("oom", 0)),
        "oom_kill": int(events.get("oom_kill", 0)),
    }


def block_source_network(event, args):
    if event == "socket.connect":
        address = args[1]
        if not (isinstance(address, tuple) and address[:2] == ("127.0.0.1", 5432)):
            raise RuntimeError("CI_SOURCE_NETWORK_FORBIDDEN")
    if event == "urllib.Request":
        raise RuntimeError("CI_SOURCE_HTTP_FORBIDDEN")


def fixture_rows():
    """Yield explicit stored spellings without rewriting them in normalization."""
    for region_index, (formal, short, city) in enumerate(REGIONS):
        for index in range(3000):
            variant = index % 5
            region = (formal, short, formal[:-1], "", "확인불가")[variant]
            org = (f"{formal} {city}", f"{short}{city}", city,
                   f"{short}{city}", f"{formal}{city}")[variant]
            yield make_row(
                f"bulk-{region_index}-{index:05d}", region, org,
                layer=index % 3, amount=1000 + region_index * 3000 + index,
                department=f"CI부서{index % 3}",
            ), formal
        # Options present only in appropriation/education must remain visible.
        yield make_row(f"appropriation-{region_index}", short,
                       f"{formal} CI합성예산기관", layer=1, amount=701), formal
        yield make_row(f"education-{region_index}", short, f"{formal}교육청",
                       institution=f"{short}{city}CI합성학교", layer=2, amount=702), formal
        yield make_row(f"org-fallback-{region_index}", "확인불가", f"{short}{city}",
                       amount=703), formal
        yield make_row(f"institution-fallback-{region_index}", "", "",
                       institution=f"{formal}{city}CI합성학교", layer=2, amount=704), formal
        # A recognized region beats org; a recognized org beats institution.
        yield make_row(f"region-wins-{region_index}", "서울특별시", f"{short}{city}",
                       amount=900001), "서울특별시"
        yield make_row(f"org-wins-{region_index}", "확인불가", "서울특별시교육청",
                       institution=f"{short}{city}CI합성학교", layer=2,
                       amount=900002), "서울특별시"
        yield make_row(f"query-decoy-{region_index}", "서울특별시", "서울특별시",
                       department=f"{short} CI검색유인", amount=900003), "서울특별시"
        old = make_row(f"old-year-{region_index}", short, f"{short}{city}", amount=900004)
        old["fiscal_year"] = 2025
        yield old, formal
    # Gyeonggi municipality 광주시 must not become Gwangju metropolitan.
    yield make_row("gyeonggi-ambiguous", "", "광주시", amount=900005), ""
    yield make_row("gwangju-formal", "광주광역시", "광주광역시 북구", amount=705), "광주광역시"


def make_row(key, region, organization, *, institution="", layer=0, amount=100,
             department="CI부서0"):
    return {
        "dataset": ("budget", "budget_appropriation", "education_budget")[layer],
        "record_key": key, "source_system": "CI_SYNTHETIC_ONLY",
        "source_operation": ("QWGJK", "AIDFA", "EDUCATION")[layer],
        "source_layer": LAYERS[layer], "fiscal_year": 2026,
        "snapshot_date": "2026-10-07", "region_name": region,
        "org_name": organization, "institution_name": institution,
        "dept_name": department, "project_code": key,
        "project_name": f"CI synthetic lighting {key}", "budget_amount": amount,
        "appropriation_amount": amount if layer == 1 else 0,
        "executed_amount": 25, "remaining_amount": amount - 25,
        "payload_sha256": hashlib.sha256(key.encode()).hexdigest(),
        "updated_at": "2026-10-07T00:00:00+00:00",
    }


def seed(engine, tables):
    from sqlalchemy import insert
    expected = {formal: {"count": 0, "budget": 0, "executed": 0, "keys": set()}
                for formal, _, _ in REGIONS}
    batch = []
    count = 0

    def flush(rows):
        with engine.begin() as conn:
            conn.execute(insert(tables["projects"]), [row for row, _ in rows])
            conn.execute(insert(tables["states"]), [{
                "dataset": row["dataset"], "record_key": row["record_key"],
                "observation_id": row["payload_sha256"][:32],
                "payload_sha256": row["payload_sha256"], "source_date": "2026-10-07",
                "last_seen_at": row["updated_at"],
            } for row, _ in rows])

    for row, canonical in fixture_rows():
        if canonical in expected and row["fiscal_year"] == 2026:
            target = expected[canonical]
            target["count"] += 1
            target["budget"] += row["budget_amount"]
            target["executed"] += row["executed_amount"]
            target["keys"].add(row["record_key"])
        batch.append((row, canonical))
        count += 1
        if len(batch) == 250:
            flush(batch)
            batch.clear()
    if batch:
        flush(batch)
    with engine.begin() as conn:
        conn.execute(insert(tables["checkpoints"]), {
            "dataset": "budget", "scope_key": "ci:preserve:cursor",
            "cursor_value": "165/478", "page_no": 165, "status": "RUNNING",
            "updated_at": "2026-10-07T00:00:00+00:00",
        })
    return count, expected


def fingerprints(engine, tables):
    from sqlalchemy import text
    result = {}
    with engine.connect() as conn:
        for name, table in tables.items():
            if name == "metadata":
                continue
            # Hash on the DB side: no full-table Python materialization.
            qualified = f'"{CI_SCHEMA}"."{table.name}"'
            result[name] = tuple(conn.execute(text(
                "SELECT count(*), md5(COALESCE(string_agg(md5(row_to_json(x)::text), "
                "'' ORDER BY row_to_json(x)::text), '')) FROM " + qualified + " x"
            )).one())
    return result


def regex_equivalence(engine):
    import admin_geography_v41 as geography
    from sqlalchemy import Text, case, column, select, values
    samples = {"", "확인불가", "광주시", "광주시청", "광주 북구", "경기도 광주시", "경기광주시청"}
    for formal, short, city in REGIONS:
        for prefix in (formal, formal[:-1], short):
            for suffix in ("", " " + city, city, city + "청", "교육청", "도청", "도시공사",
                           " CI합성기관", "광주시", "성", "시"):
                samples.add(prefix + suffix)
                samples.add(" \t" + prefix + suffix)
    for formal in geography.REGIONS:
        samples.update((formal, formal + "교육청"))
    fixture = values(column("raw", Text), name="raw_region_patterns").data(
        [(value,) for value in sorted(samples)]
    ).alias("sample")
    # Exact original pattern strings are bound to PostgreSQL's native ~ operator.
    canonical = case(*[(fixture.c.raw.regexp_match(pattern), region)
                       for pattern, region in geography.region_prefix_rules()], else_="")
    with engine.connect() as conn:
        rows = conn.execute(select(fixture.c.raw, canonical)).all()
    for raw, actual in rows:
        assert actual == geography.canonical_region(raw), (raw, actual, geography.canonical_region(raw))
    return len(rows)


def excel_contract(rows):
    import budget_excel_vnext
    data = budget_excel_vnext.build_budget_xlsx(rows)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    exported = sheet.findall("m:sheetData/m:row", ns)[1:]
    assert len(exported) == len(rows)
    keys, amount = set(), 0
    for item in exported:
        row_no = item.attrib["r"]
        keys.add(item.find(f"m:c[@r='G{row_no}']/m:is/m:t", ns).text)
        amount += int(item.find(f"m:c[@r='L{row_no}']/m:v", ns).text)
    assert keys == {row["record_key"] for row in rows}
    assert amount == sum(row["budget_amount"] for row in rows)
    return len(data)


def read_contract(engine, expected):
    import budget_read_vnext as reader
    from sqlalchemy import event
    observed = {"select": 0, "writes": 0, "page_limit_200": 0, "aggregate": 0}

    def capture(_conn, _cursor, statement, _parameters, context, _executemany):
        command = statement.lstrip().split(None, 1)[0].upper()
        if command not in {"SELECT", "SHOW", "SET"}:
            observed["writes"] += 1
            raise AssertionError("READ_PATH_ISSUED_NON_READ_SQL: " + command)
        if command == "SELECT":
            observed["select"] += 1
        compiled = context.compiled
        if compiled is not None:
            limit = getattr(compiled.statement, "_limit_clause", None)
            if limit is not None and getattr(limit, "value", None) == 200:
                observed["page_limit_200"] += 1
        if "count(" in statement.lower() and "sum(" in statement.lower():
            observed["aggregate"] += 1

    event.listen(engine, "before_cursor_execute", capture)
    try:
        for formal, short, city in REGIONS:
            target = expected[formal]
            prior_keys = None
            # Repeated reads must keep whole-filter totals despite bounded pages.
            for selected in (formal, short, formal[:-1], short):
                scope = dict(fiscal_year=2026, source_layers=LAYERS, region=selected)
                rows = reader.screen_budget_rows(**scope, limit=200)
                assert len(rows) == 200
                keys = {row["record_key"] for row in rows}
                assert keys <= target["keys"]
                assert all(reader.region_matches(row, formal) for row in rows)
                if prior_keys is not None:
                    assert keys == prior_keys
                prior_keys = keys
                summary = reader.screen_budget_summary(**scope)
                assert summary["project_count"] == target["count"]
                assert summary["project_count"] > len(rows)
                assert summary["budget_total"] == target["budget"]
                assert summary["executed_total"] == target["executed"]
                assert summary["remaining_total"] == target["budget"] - target["executed"]
                assert summary["source_io_performed"] is False
                names = reader.budget_institution_names(fiscal_year=2026, region=selected)
                assert reader.institution_name_allowed(formal, city, names)
                assert f"{formal} CI합성예산기관" in names
                assert f"{formal}교육청" in names
                assert f"{short}{city}CI합성학교" in names
                city_forms = set(reader.institution_name_forms(formal, city))
                assert sum(bool(city_forms.intersection(reader.institution_name_forms(formal, name)))
                           for name in names) == 1
                departments = reader.budget_department_names(**scope, institution_name=city)
                assert set(departments) == {"CI부서0", "CI부서1", "CI부서2"}
                for institution in (city, f"{short}{city}", f"{formal} {city}"):
                    filtered = reader.screen_budget_rows(**scope, institution_name=institution, limit=200)
                    assert len(filtered) == 200
                    total = reader.screen_budget_summary(**scope, institution_name=institution)
                    assert total["project_count"] == 3001
                    assert total["budget_total"] == target["budget"] - 701 - 702 - 704
                excel_contract(rows)
            # Verify all matched keys through bounded pages; SQL filters precede paging.
            seen = set()
            for offset in range(0, target["count"], 200):
                page = reader.screen_budget_rows(fiscal_year=2026, source_layers=LAYERS,
                                                region=short, limit=200, offset=offset)
                keys = {row["record_key"] for row in page}
                assert not seen.intersection(keys)
                seen.update(keys)
            assert seen == target["keys"]
        rows = reader.screen_budget_rows(fiscal_year=2026, source_layers=LAYERS,
                                        region="광주", limit=200)
        assert {row["record_key"] for row in rows} == {"gwangju-formal"}
        assert observed["writes"] == 0 and observed["page_limit_200"] > 0
        assert observed["aggregate"] > 0
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    return observed


def run():
    if not authorized():
        raise RuntimeError("CI_EPHEMERAL_REGION_READ_AUTHORIZATION_REQUIRED")
    before_memory = memory_snapshot()
    assert before_memory["cgroup_limit_bytes"] == 256 * MIB, before_memory
    assert before_memory["swap_limit_bytes"] == 0, before_memory
    assert before_memory["oom"] == before_memory["oom_kill"] == 0, before_memory
    sys.addaudithook(block_source_network)
    from sqlalchemy import select, text
    import budget_pg_store as store
    import g2b_database
    engine = g2b_database.engine()
    with engine.connect() as conn:
        assert conn.dialect.name == "postgresql"
        assert conn.execute(text("SHOW server_version_num")).scalar_one().startswith("16")
        assert conn.execute(text("SELECT current_database()")).scalar_one() == "g2b_region_reads_4304_ci"
        # Never reuse, truncate, delete, or reset an existing schema.
        assert not conn.execute(text("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=:n)"),
                                {"n": CI_SCHEMA}).scalar_one(), "CI_SCHEMA_ALREADY_EXISTS"
    engine, tables = store._engine_and_tables()
    fixture_count, expected = seed(engine, tables)
    fingerprints_before = fingerprints(engine, tables)
    # The CI role belongs solely to this disposable service. New connections
    # must prove server-enforced read-only transactions before reader calls.
    with engine.begin() as conn:
        conn.execute(text("ALTER ROLE postgres SET default_transaction_read_only = on"))
    engine.dispose()
    with engine.connect() as conn:
        assert conn.execute(text("SHOW default_transaction_read_only")).scalar_one() == "on"
        assert conn.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
    regex_samples = regex_equivalence(engine)
    observed = read_contract(engine, expected)
    assert fingerprints(engine, tables) == fingerprints_before, "READS_CHANGED_STORED_FACTS_OR_CURSOR"
    with engine.connect() as conn:
        checkpoint = dict(conn.execute(select(tables["checkpoints"])).mappings().one())
    assert (checkpoint["cursor_value"], checkpoint["page_no"], checkpoint["status"]) == (
        "165/478", 165, "RUNNING",
    )
    memory = memory_snapshot()
    assert memory["rss_peak_bytes"] < 192 * MIB, memory
    assert memory["cgroup_peak_bytes"] < 192 * MIB, memory
    assert memory["cgroup_limit_bytes"] - memory["cgroup_peak_bytes"] >= 64 * MIB, memory
    assert memory["oom"] == memory["oom_kill"] == 0, memory
    return {
        "status": "PASS", "scope": "standalone_read_only_budget_reader",
        "not_proven": "combined web + worker + PostgreSQL memory or production data",
        "postgres_major": 16, "schema": CI_SCHEMA, "fixture_rows": fixture_count,
        "batch_limit": 250, "regex_samples": regex_samples,
        "regions": {region: {k: v for k, v in data.items() if k != "keys"}
                    for region, data in expected.items()},
        "sql": observed, "transaction_read_only": True,
        "stored_facts_and_checkpoint_unchanged": True,
        "preserved_checkpoint": {key: checkpoint[key] for key in ("cursor_value", "page_no", "status")},
        "source_api_calls": 0, "collector_started": False,
        "reserve_bytes": 64 * MIB, "memory": memory,
    }


def main():
    started = time.monotonic()
    try:
        report = run()
        code = 0
    except Exception as exc:
        report = {"status": "FAIL", "error": type(exc).__name__, "message": str(exc)}
        traceback.print_exc()
        code = 1
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True), flush=True)
    # Only the authorized CI environment may produce the fixed report artifact.
    if authorized():
        report_path = Path("/results/g2b-region-reads-4304.json")
        report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
