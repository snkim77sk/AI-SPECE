"""Dynamic source workers never deserialize unbounded historical budget rows."""
import pytest
from sqlalchemy import String, event, select
from sqlalchemy.dialects import postgresql

import budget_pg_store
import budget_storage
import classification_vnext
import db
import memory_guard
import g2b_dynamic_source_budget as dynamic
import vnext_schema


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("G2B_BUDGET_STORAGE", "postgresql")
    monkeypatch.setenv("G2B_BUDGET_DATABASE_URL", f"sqlite:///{tmp_path / 'stored-budget.sqlite3'}")
    for name in ("G2B_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"):
        monkeypatch.delenv(name, raising=False)
    budget_pg_store.reset_engine_cache()
    yield budget_pg_store._engine_and_tables()
    budget_pg_store.reset_engine_cache()


def _preserve(dataset="budget", key="key", name="LED 조명 교체사업"):
    return budget_storage.preserve_raw(dataset, key, {
        "fyr": "2026", "dbiz_nm": name, "project_name": name,
        "fld_nm": name, "sect_nm": "도로", "bdg_cash_amt": "1000",
        "biz_bdg_tott_amt": "1000", "budget_amount": "1000",
    })


def _dynamic(monkeypatch):
    reserves, commits = [], []
    monkeypatch.setattr(memory_guard, "source_dynamic_budget_enabled", lambda: True)
    monkeypatch.setattr(memory_guard, "source_allocation_checkpoint", lambda **kw: reserves.append(kw["reserve_bytes"]))
    monkeypatch.setattr(dynamic, "record_classification_commit", lambda *, rows: commits.append(rows))
    return reserves, commits


def _classifications(dataset="budget"):
    primary = budget_pg_store.classification_rows([dataset], vnext_schema.CLASSIFIER_VERSION)
    with db.connect() as conn:
        compat = [dict(row) for row in conn.execute(
            "SELECT entity_key,source_payload_sha256,primary_category FROM classifications "
            "WHERE entity_type=? AND classifier_version=? ORDER BY entity_key",
            (dataset, vnext_schema.CLASSIFIER_VERSION),
        )]
    return primary, compat


@pytest.mark.parametrize("dataset", sorted(budget_storage.BUDGET_DATASETS))
def test_dynamic_projection_preserves_all_budget_classifier_inputs(monkeypatch, store, dataset):
    _preserve(dataset)
    legacy = next(budget_storage.current_raw_for_keys(dataset, ["key"]))[0]
    reserves, _commits = _dynamic(monkeypatch)
    bounded = next(budget_storage.current_raw_for_keys(dataset, ["key"]))[0]
    assert bounded == legacy
    assert classification_vnext.classify_payload(dataset, bounded["payload"]) == classification_vnext.classify_payload(dataset, legacy["payload"])
    assert reserves == [2 * 1024 * 1024]


def test_dynamic_batches_one_row_and_marks_only_committed_exact_hashes(monkeypatch, store):
    hashes = {f"key-{index}": _preserve(key=f"key-{index}") for index in range(3)}
    reserves, commits = _dynamic(monkeypatch)
    original = dynamic.record_classification_commit

    def committed(*, rows):
        primary, compat = _classifications()
        assert len(primary) == len(compat) == len(commits) + rows
        assert all(row["source_payload_sha256"] == hashes[row["record_key"]] for row in primary)
        assert all(row["source_payload_sha256"] == hashes[row["entity_key"]] for row in compat)
        original(rows=rows)

    monkeypatch.setattr(dynamic, "record_classification_commit", committed)
    result = classification_vnext.classify_dataset("budget", batch_size=2, max_batches=1)
    assert result["batch_size"] == 2
    assert result["physical_batch_size"] == 1
    assert result["classified"] == result["payload_rows_loaded"] == result["physical_batches_done"] == 2
    assert result["batches_done"] == 1
    assert result["batch_limit_reached"] is True
    assert commits == [1, 1]
    assert reserves and min(reserves) == 2 * 1024 * 1024
    assert max(reserves) < 3 * 1024 * 1024
    result = classification_vnext.classify_dataset("budget", batch_size=2, max_batches=1)
    assert result["classified"] == result["batches_done"] == 1
    assert result["physical_batches_done"] == 1
    assert result["batch_limit_reached"] is False
    assert commits == [1, 1, 1]
    assert len(_classifications()[0]) == 3


def test_dynamic_never_selects_historical_amounts_json(monkeypatch, store):
    engine, tables = store
    _preserve()
    # Neither byte length nor depth of irrelevant historic JSON can allocate
    # client-side memory. It is absent from the SQL projection entirely.
    nested = {"leaf": "x" * 300_000}
    for _ in range(40):
        nested = {"child": nested}
    with engine.begin() as conn:
        conn.execute(tables["projects"].update().values(amounts=nested))
    statements = []

    def capture(_conn, clause, *_args):
        if getattr(clause, "is_select", False) and "source_row_within_limit" in str(clause):
            statements.append(clause)

    event.listen(engine, "before_execute", capture)
    _dynamic(monkeypatch)
    try:
        result = classification_vnext.classify_dataset("budget", max_batches=1)
    finally:
        event.remove(engine, "before_execute", capture)
    assert result["classified"] == 1
    assert statements
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "amounts" not in sql
    assert "octet_length" not in sql  # SQLite execution uses byte length on BLOB.
    assert "CASE WHEN anon_1.source_row_within_limit THEN" in sql
    assert len(sql) < 15_000
    with engine.connect() as conn:
        assert conn.execute(select(tables["projects"].c.amounts)).scalar_one() == nested


@pytest.mark.parametrize("project_name", ["x" * (64 * 1024 + 1), "x" * (64 * 1024 - 3) + "😀"])
def test_oversized_stored_field_is_null_before_driver_and_holds_without_writes(monkeypatch, store, project_name):
    engine, tables = store
    digest = _preserve()
    classification_vnext.classify_dataset("budget")
    before = _classifications()
    with engine.begin() as conn:
        conn.execute(tables["projects"].update().values(project_name=project_name))
    statements = []

    def capture(_conn, clause, *_args):
        if getattr(clause, "is_select", False) and "source_row_within_limit" in str(clause):
            statements.append(clause)

    event.listen(engine, "before_execute", capture)
    _reserves, commits = _dynamic(monkeypatch)
    try:
        with pytest.raises(memory_guard.MemoryPressureError, match="SOURCE_RESPONSE_LIMIT:stored classification row bytes") as caught:
            classification_vnext.classify_dataset("budget", force=True, max_batches=1)
    finally:
        event.remove(engine, "before_execute", capture)
    assert type(caught.value) is memory_guard.MemoryPressureError
    assert commits == []
    assert _classifications() == before
    with engine.connect() as conn:
        guarded = conn.execute(statements[-1]).mappings().one()
        assert guarded["source_row_within_limit"] is False
        assert guarded["project_name"] is None
        assert guarded["record_key"] is None
        assert guarded["payload_sha256"] is None
        assert conn.execute(select(tables["projects"].c.project_name)).scalar_one() == project_name
        assert conn.execute(select(tables["states"].c.payload_sha256)).scalar_one() == digest


def test_exact_multibyte_field_boundary_is_allowed(monkeypatch, store):
    engine, tables = store
    _preserve()
    name = "x" * (64 * 1024 - 4) + "😀"
    with engine.begin() as conn:
        conn.execute(tables["projects"].update().values(project_name=name))
    _dynamic(monkeypatch)
    row = next(budget_pg_store.current_rows_for_keys("budget", ["key"]))[0]
    assert row["payload"]["project_name"] == name


def test_aggregate_byte_boundary_includes_identity_and_state_metadata(monkeypatch, store):
    engine, tables = store
    _preserve()
    state, projects = tables["states"], tables["projects"]
    # Measure the exact scalar projection, including both hash values and state
    # timestamps. Enforce a small test cap so varchar limits remain realistic.
    columns = [column for column in projects.c if isinstance(column.type, String)]
    columns.extend((state.c.source_date, state.c.last_seen_at, state.c.payload_sha256))
    with engine.connect() as conn:
        values = conn.execute(select(*columns).select_from(state.join(projects,
            (state.c.dataset == projects.c.dataset) & (state.c.record_key == projects.c.record_key)
        ))).one()
    size = sum(len(value.encode("utf-8")) for value in values)
    _dynamic(monkeypatch)
    monkeypatch.setattr(budget_pg_store, "SOURCE_CLASSIFICATION_ROW_BYTES", size)
    assert next(budget_pg_store.current_rows_for_keys("budget", ["key"]))[0]["record_key"] == "key"
    monkeypatch.setattr(budget_pg_store, "SOURCE_CLASSIFICATION_ROW_BYTES", size - 1)
    with pytest.raises(memory_guard.MemoryPressureError, match="stored classification row bytes"):
        next(budget_pg_store.current_rows_for_keys("budget", ["key"]))


def test_projection_state_hash_mismatch_is_explicit_hold(monkeypatch, store):
    engine, tables = store
    _preserve()
    with engine.begin() as conn:
        conn.execute(tables["states"].update().values(payload_sha256="different-current-hash"))
    _reserves, commits = _dynamic(monkeypatch)
    with pytest.raises(memory_guard.MemoryPressureError, match="stored classification hash mismatch"):
        classification_vnext.classify_dataset("budget", max_batches=1)
    assert commits == []
    assert _classifications() == ([], [])


@pytest.mark.parametrize("dataset", sorted(budget_storage.BUDGET_DATASETS))
def test_hash_drift_before_write_preserves_prior_classification_without_progress(monkeypatch, store, dataset):
    _preserve(dataset)
    classification_vnext.classify_dataset(dataset)
    before = _classifications(dataset)
    _reserves, commits = _dynamic(monkeypatch)
    original = classification_vnext.classify_payload

    def change_source(dataset, payload):
        _preserve(dataset, name="태양광 신규 사업")
        return original(dataset, payload)

    monkeypatch.setattr(classification_vnext, "classify_payload", change_source)
    result = classification_vnext.classify_dataset(dataset, force=True, max_batches=1)
    assert result["classified"] == 0
    assert commits == []
    assert _classifications(dataset) == before
    assert list(budget_pg_store.pending_classification_key_batches(dataset, vnext_schema.CLASSIFIER_VERSION)) == [["key"]]


@pytest.mark.parametrize("dataset", sorted(budget_storage.BUDGET_DATASETS))
def test_compat_write_failure_rolls_back_primary_and_never_marks_progress(monkeypatch, store, dataset):
    _preserve(dataset)
    classification_vnext.classify_dataset(dataset)
    before = _classifications(dataset)
    _preserve(dataset, name="태양광 변경 사업")
    _reserves, commits = _dynamic(monkeypatch)

    def fail(*_args, **_kwargs):
        raise RuntimeError("compat write failed")

    monkeypatch.setattr(classification_vnext, "save_compat_classification", fail)
    with pytest.raises(RuntimeError, match="compat write failed"):
        classification_vnext.classify_dataset(dataset, max_batches=1)
    assert commits == []
    assert _classifications(dataset) == before


def test_allocation_hold_precedes_stored_payload_fetch(monkeypatch, store):
    _preserve()
    _dynamic(monkeypatch)

    def hold(**_kwargs):
        raise memory_guard.MemoryPressureError("SOURCE_DYNAMIC_ALLOCATION_HOLD:1")

    def forbidden(_row):
        raise AssertionError("row must not reach adapter")

    monkeypatch.setattr(memory_guard, "source_allocation_checkpoint", hold)
    monkeypatch.setattr(budget_pg_store, "_current_projection_row", forbidden)
    with pytest.raises(memory_guard.MemoryPressureError, match="SOURCE_DYNAMIC_ALLOCATION_HOLD"):
        next(budget_pg_store.current_rows_for_keys("budget", ["key"]))


def test_postgresql_statement_bounds_scalars_and_locks_current_source(monkeypatch, store):
    from contextlib import nullcontext
    from types import SimpleNamespace

    _engine, tables = store
    statements = []

    class Result:
        def mappings(self):
            return self
        def all(self):
            return []
        def scalar_one_or_none(self):
            return 1

    class Connection:
        def execute(self, statement):
            statements.append(statement)
            return Result()

    connection = Connection()
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"),
                             connect=lambda: nullcontext(connection))
    monkeypatch.setattr(budget_pg_store, "_engine_and_tables", lambda: (engine, tables))
    _dynamic(monkeypatch)
    assert list(budget_pg_store.current_rows_for_keys("budget", ["key"], batch_size=500)) == []
    sql = str(statements[-1].compile(dialect=postgresql.dialect()))
    assert "octet_length(budget_projects.project_name)" in sql
    assert "octet_length(budget_record_states.record_key)" not in sql
    assert "octet_length(budget_projects.record_key)" in sql
    assert "octet_length(budget_record_states.payload_sha256)" in sql
    assert "amounts" not in sql
    assert "CASE WHEN anon_1.source_row_within_limit THEN anon_1.project_name END" in sql
    assert len(sql) < 15_000
    assert budget_pg_store.classification_source_is_current("budget", "key", "digest", _conn=connection)
    sql = str(statements[-1].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF budget_record_states, budget_projects" in sql
    assert "budget_record_states.payload_sha256 =" in sql
    assert "budget_projects.payload_sha256 =" in sql


def test_legacy_classification_keeps_requested_batch_size(monkeypatch, store):
    for index in range(3):
        _preserve(key=f"key-{index}")
    monkeypatch.setattr(memory_guard, "source_dynamic_budget_enabled", lambda: False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("legacy path must not use dynamic source policy")

    monkeypatch.setattr(memory_guard, "source_allocation_checkpoint", forbidden)
    monkeypatch.setattr(dynamic, "record_classification_commit", forbidden)
    result = classification_vnext.classify_dataset("budget", batch_size=500, max_batches=2)
    assert result["batch_size"] == 500
    assert result["batches_done"] == 1
    assert result["classified"] == 3


@pytest.mark.parametrize("dataset",sorted(budget_storage.BUDGET_DATASETS))
def test_legal_wide_whitespace_aliases_have_preflight_above_measured_peak(dataset):
    import tracemalloc
    import budget_normalizer_v41
    name=" "+"x"*(64*1024-6)+"😀 "
    extra=" "+"y"*(63*1024-6)+"😀 "
    assert len(name.encode("utf-8")) == 64*1024
    payload=budget_normalizer_v41.compat_payload(dataset,{
        "project_name":name,"field_name":extra if dataset=="budget_appropriation" else "",
        "section_name":extra if dataset=="education_budget" else "",
    })
    reserve=classification_vnext._source_classification_reserve_bytes(dataset,payload)
    tracemalloc.start()
    try:
        classification_vnext.classify_payload(dataset,payload)
        peak=tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert reserve > peak > 2*1024*1024
    assert classification_vnext._source_classification_reserve_bytes(dataset,{"project_name":"LED"}) < 3*1024*1024


def test_large_classifier_step_holds_after_read_before_text_allocation(monkeypatch,store):
    engine,tables=store
    _preserve()
    with engine.begin() as conn:
        conn.execute(tables["projects"].update().values(project_name=" "+"x"*(64*1024-6)+"😀 "))
    reserves,commits=_dynamic(monkeypatch)
    def checkpoint(*,reserve_bytes):
        reserves.append(reserve_bytes)
        if reserve_bytes > 3*1024*1024:
            raise memory_guard.MemoryPressureError("SOURCE_DYNAMIC_ALLOCATION_HOLD:classifier")
    monkeypatch.setattr(memory_guard,"source_allocation_checkpoint",checkpoint)
    monkeypatch.setattr(classification_vnext,"classify_payload",lambda *a:pytest.fail("must hold before strip/join/casefold"))
    with pytest.raises(memory_guard.MemoryPressureError,match="classifier"):
        classification_vnext.classify_dataset("budget",batch_size=500,max_batches=2)
    assert max(reserves) > 8*1024*1024
    assert commits == [] and _classifications() == ([],[])


@pytest.mark.parametrize("dataset,field", [
    ("budget", "project_name"),
    ("budget_appropriation", "project_name"),
    ("budget_appropriation", "field_name"),
    ("budget_appropriation", "section_name"),
    ("budget_appropriation", "account_name"),
    ("education_budget", "project_name"),
    ("education_budget", "section_name"),
    ("education_budget", "account_name"),
])
def test_narrow_projection_preserves_every_classifier_alias(monkeypatch, store, dataset, field):
    engine, tables = store
    _preserve(dataset, name="ordinary project")
    with engine.begin() as conn:
        conn.execute(tables["projects"].update().values(**{field: "태양광 발전시설"}))
    legacy = next(budget_storage.current_raw_for_keys(dataset, ["key"]))[0]
    _reserves, commits = _dynamic(monkeypatch)
    bounded = next(budget_pg_store.current_rows_for_keys(
        dataset, ["key"], classification_only=True
    ))[0]
    fields = classification_vnext.TEXT_FIELDS[dataset]
    assert {key: legacy["payload"].get(key) for key in fields} == {
        key: bounded["payload"].get(key) for key in fields
    }
    expected = classification_vnext.classify_payload(dataset, legacy["payload"])
    assert expected["primary_category"] == "SOLAR"
    assert classification_vnext.classify_payload(dataset, bounded["payload"]) == expected
    result = classification_vnext.classify_dataset(dataset)
    assert result["classified"] == 1
    primary, compat = _classifications(dataset)
    assert primary[0]["primary_category"] == compat[0]["primary_category"] == "SOLAR"
    assert commits == [1]


@pytest.mark.parametrize("dataset", sorted(budget_storage.BUDGET_DATASETS))
def test_classifier_never_selects_irrelevant_scalar_facts(monkeypatch, store, dataset):
    engine, tables = store
    _preserve(dataset)
    # Old malformed facts outside classifier inputs should not require decoding.
    with engine.begin() as conn:
        conn.execute(tables["projects"].update().values(
            org_name="x" * 200_000, amounts={"irrelevant": "x" * 300_000}
        ))
    statements = []
    def capture(_conn, clause, *_args):
        if getattr(clause, "is_select", False) and "source_row_within_limit" in str(clause):
            statements.append(clause)
    event.listen(engine, "before_execute", capture)
    _dynamic(monkeypatch)
    try:
        result = classification_vnext.classify_dataset(dataset)
    finally:
        event.remove(engine, "before_execute", capture)
    assert result["classified"] == 1
    assert len(statements) == 1
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    for excluded in ("amounts", "org_name", "region_name", "budget_amount", "source_system"):
        assert excluded not in sql
    assert "pg_column_size" not in sql
    assert "source_row_within_limit" in sql


def test_commit_exit_failure_never_reports_classification_progress(monkeypatch, store):
    from contextlib import contextmanager
    _preserve()
    _dynamic(monkeypatch)
    real_connect = classification_vnext.connect
    events = []
    @contextmanager
    def failing_commit():
        with real_connect() as conn:
            yield conn
            # Only the write transaction (not schema initialization) has run writes.
            if conn.in_transaction:
                conn.rollback()
                raise RuntimeError("commit failed")
    monkeypatch.setattr(classification_vnext, "connect", failing_commit)
    monkeypatch.setattr(dynamic, "record_classification_commit", lambda **kw: events.append(kw))
    with pytest.raises(RuntimeError, match="commit failed"):
        classification_vnext.classify_dataset("budget")
    assert events == []


@pytest.mark.parametrize("failure", [None, "compat", "commit"])
def test_shared_one_plus_one_pool_commits_both_tables_before_progress(monkeypatch, store, failure):
    from contextlib import contextmanager
    from sqlalchemy import Column, Float, Integer, MetaData, Table, Text, UniqueConstraint, create_engine, func

    previous_engine, tables = store
    _preserve()
    # A lease occupies one slot throughout, as in the real source worker.
    engine = create_engine(previous_engine.url, pool_size=1, max_overflow=1, pool_timeout=0.05)
    metadata = MetaData()
    compat = Table("classifications", metadata,
        Column("id", Integer, primary_key=True),
        Column("entity_type", Text), Column("entity_key", Text),
        Column("primary_category", Text), Column("subcategory", Text),
        Column("confidence", Float), Column("reason", Text),
        Column("classifier_version", Text), Column("source_payload_sha256", Text),
        Column("classified_at", Text, server_default=func.current_timestamp()),
        UniqueConstraint("entity_type", "entity_key", "classifier_version"),
    )
    metadata.create_all(engine)
    monkeypatch.setattr(budget_pg_store, "_engine_and_tables", lambda: (engine, tables))
    _dynamic(monkeypatch)
    events = []

    class Adapter:
        def __init__(self, raw):
            self._conn = raw
            self.writing = False
        def execute(self, sql, parameters=()):
            if sql == "BEGIN IMMEDIATE":
                self.writing = True
                return None
            return self._conn.exec_driver_sql(sql, parameters)

    @contextmanager
    def shared_connect():
        with engine.connect() as raw:
            adapter = Adapter(raw)
            try:
                yield adapter
                if failure == "commit" and adapter.writing:
                    raise RuntimeError("shared commit failed")
                raw.commit()
            except Exception:
                raw.rollback()
                raise

    monkeypatch.setattr(classification_vnext, "connect", shared_connect)
    monkeypatch.setattr(classification_vnext, "ensure_vnext_schema", lambda _conn: None)
    original_save = classification_vnext.save_compat_classification
    def save_compat(*args, **kwargs):
        original_save(*args, **kwargs)
        if failure == "compat":
            raise RuntimeError("shared compat failed")
    monkeypatch.setattr(classification_vnext, "save_compat_classification", save_compat)

    def counts():
        with engine.connect() as conn:
            return (
                conn.execute(select(func.count()).select_from(tables["classifications"])).scalar_one(),
                conn.execute(select(func.count()).select_from(compat)).scalar_one(),
            )
    def committed(*, rows):
        assert counts() == (1, 1)
        events.append(rows)
    monkeypatch.setattr(dynamic, "record_classification_commit", committed)
    monkeypatch.setattr(engine, "begin", lambda: pytest.fail("independent third checkout exhausts 1+1 pool"))
    try:
        with engine.connect():
            if failure:
                with pytest.raises(RuntimeError, match=f"shared {failure} failed"):
                    classification_vnext.classify_dataset("budget")
                assert counts() == (0, 0)
                assert events == []
            else:
                result = classification_vnext.classify_dataset("budget")
                assert result["classified"] == 1
                assert events == [1]
    finally:
        engine.dispose()


@pytest.mark.parametrize("dataset", sorted(budget_storage.BUDGET_DATASETS))
def test_small_classifier_uses_precise_headroom_not_eight_mib_floor(monkeypatch, dataset):
    import budget_normalizer_v41
    payload = budget_normalizer_v41.compat_payload(dataset, {"project_name": "LED"})
    reserve = classification_vnext._source_classification_reserve_bytes(dataset, payload)
    assert reserve < 3 * dynamic.MIB
    monkeypatch.setenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_SOURCE_ONLY_RUNTIME", "1")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    profile = {
        "source": "cgroup-v2", "limit_bytes": dynamic.CGROUP_LIMIT_BYTES,
        "current_bytes": dynamic.SOURCE_CEILING_BYTES - reserve - dynamic.ALLOCATION_DRIFT_BYTES - 1,
    }
    monkeypatch.setattr(memory_guard, "container_budget_snapshot", lambda: profile)
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 80.0)
    monkeypatch.setattr(memory_guard, "soft_limit_mib", lambda: 112.0)
    memory_guard.source_allocation_checkpoint(reserve_bytes=reserve)
    assert classification_vnext.classify_payload(dataset, payload)["primary_category"] == "LIGHTING"
    profile["current_bytes"] += 1
    with pytest.raises(memory_guard.MemoryPressureError, match="CGRP_DYNAMIC_ALLOCATION_HOLD"):
        memory_guard.source_allocation_checkpoint(reserve_bytes=reserve)


def test_large_backlog_finishes_with_original_logical_work_budget(monkeypatch, store):
    from sqlalchemy import insert
    engine, tables = store
    _preserve(key="key-0000")
    # Seed an existing normalized backlog outside the bounded read/classify path.
    with engine.begin() as conn:
        project = dict(conn.execute(select(tables["projects"])).mappings().one())
        state = dict(conn.execute(select(tables["states"])).mappings().one())
        conn.execute(insert(tables["projects"]), [
            {**project, "record_key": f"key-{index:04d}"} for index in range(1, 2477)
        ])
        conn.execute(insert(tables["states"]), [
            {**state, "record_key": f"key-{index:04d}"} for index in range(1, 2477)
        ])
    _reserves, commits = _dynamic(monkeypatch)
    original_pending = budget_pg_store.pending_classification_key_batches
    original_rows = budget_pg_store.current_rows_for_keys
    reads = 0
    def pending(*args, **kwargs):
        assert kwargs["batch_size"] == 1
        for keys in original_pending(*args, **kwargs):
            assert len(keys) == 1
            yield keys
    def rows(*args, **kwargs):
        nonlocal reads
        assert kwargs["batch_size"] == 1
        for batch in original_rows(*args, **kwargs):
            assert len(batch) == 1
            reads += 1
            yield batch
    monkeypatch.setattr(budget_pg_store, "pending_classification_key_batches", pending)
    monkeypatch.setattr(budget_pg_store, "current_rows_for_keys", rows)
    result = classification_vnext.classify_dataset("budget", batch_size=500, max_batches=32)
    assert result["classified"] == result["payload_rows_loaded"] == reads == 2477
    assert result["batch_size"] == 500
    assert result["batches_done"] == 5
    assert result["physical_batch_size"] == 1
    assert result["physical_batches_done"] == 2477
    assert result["batch_limit_reached"] is False
    assert len(commits) == sum(commits) == 2477
    primary, compat = _classifications()
    assert len(primary) == len(compat) == 2477
    assert list(original_pending("budget", vnext_schema.CLASSIFIER_VERSION, batch_size=1)) == []
