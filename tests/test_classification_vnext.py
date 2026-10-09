import db
import budget_pg_store
import budget_storage
import classification_vnext
import vnext_schema
import vnext_store


def _fresh_db(monkeypatch, tmp_path):
    path = tmp_path / "classification.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    vnext_store.ensure_foundation()
    return path


def test_production_shopping_classification_is_ingest_only(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "0")

    def forbidden_connect(*_args, **_kwargs):
        raise AssertionError("production shopping post-classification must not touch DB")

    monkeypatch.setattr(classification_vnext, "connect", forbidden_connect)
    report = classification_vnext.classify_dataset("shopping_delivery")

    assert report["classified"] == 0
    assert report["storage"] == "NORMALIZED_AT_INGEST"
    assert report["payload_rows_loaded"] == 0


def test_local_collector_shopping_classification_is_ingest_only(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")

    def forbidden_connect(*_args, **_kwargs):
        raise AssertionError("local normalized shopping classifier must not scan RAW")

    monkeypatch.setattr(classification_vnext, "connect", forbidden_connect)
    report = classification_vnext.classify_dataset("shopping_delivery")

    assert report["classified"] == 0
    assert report["storage"] == "NORMALIZED_AT_INGEST"
    assert report["payload_rows_loaded"] == 0


def test_exact_detail_item_rules_run_only_as_post_raw_classification():
    lighting = classification_vnext.classify_payload(
        "shopping_delivery",
        {"dtilPrdctClsfcNo": "3911160302", "prdctIdntNoNm": "일반명"},
    )
    pole = classification_vnext.classify_payload(
        "shopping_delivery",
        {"dtilPrdctClsfcNo": "3911152601", "prdctIdntNoNm": "일반명"},
    )
    solar = classification_vnext.classify_payload(
        "shopping_delivery",
        {"dtilPrdctClsfcNo": "2611160701", "prdctIdntNoNm": "일반명"},
    )
    assert lighting["primary_category"] == "LIGHTING"
    assert lighting["confidence"] == 1.0
    assert pole["primary_category"] == "POLE"
    assert solar["primary_category"] == "SOLAR"


def test_text_rules_classify_lighting_pole_electrical_and_solar():
    assert classification_vnext.classify_payload(
        "bid_notice_goods", {"bidNtceNm": "LED 가로등기구 구매"}
    )["subcategory"] == "STREET_LIGHT"
    assert classification_vnext.classify_payload(
        "bid_notice_goods", {"bidNtceNm": "스테인리스 가로등주 제작 구매"}
    )["primary_category"] == "POLE"
    assert classification_vnext.classify_payload(
        "bid_notice_service", {"bidNtceNm": "청사 전기설계 용역"}
    )["subcategory"] == "ELECTRICAL_DESIGN"
    assert classification_vnext.classify_payload(
        "budget", {"dbiz_nm": "공공건물 태양광 발전설비 설치"}
    )["primary_category"] == "SOLAR"


def test_non_target_rows_are_classified_other_not_discarded():
    result = classification_vnext.classify_payload(
        "bid_notice_service", {"bidNtceNm": "청사 정기 청소용역", "corpNm": "LED조명주식회사"}
    )
    assert result["primary_category"] == "OTHER"
    assert result["reason"] == "no post-RAW target-domain rule matched"


def test_classify_dataset_writes_one_result_for_every_raw_row(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    rows = [
        ("A|000", {"bidNtceNm": "LED 터널등 교체"}),
        ("B|000", {"bidNtceNm": "청사 정기 청소용역"}),
        ("C|000", {"bidNtceNm": "전기감리 용역"}),
    ]
    for key, payload in rows:
        vnext_store.preserve_raw("bid_notice_service", key, payload, source_system="G2B")

    report = classification_vnext.classify_dataset("bid_notice_service", batch_size=2)
    assert report["classified"] == 3
    assert report["counts"] == {"ELECTRICAL": 1, "LIGHTING": 1, "OTHER": 1}
    second = classification_vnext.classify_dataset("bid_notice_service", batch_size=2)
    assert second["classified"] == 0
    assert second["counts"] == {}

    with db.connect() as conn:
        raw_count = conn.execute(
            "SELECT COUNT(*) AS n FROM raw_records WHERE dataset='bid_notice_service'"
        ).fetchone()["n"]
        classified = conn.execute(
            "SELECT primary_category,COUNT(*) AS n FROM classifications "
            "WHERE entity_type='bid_notice_service' AND classifier_version=? "
            "GROUP BY primary_category ORDER BY primary_category",
            (vnext_schema.CLASSIFIER_VERSION,),
        ).fetchall()
    assert raw_count == 3
    assert {r["primary_category"]: r["n"] for r in classified} == {
        "ELECTRICAL": 1, "LIGHTING": 1, "OTHER": 1,
    }


def test_changed_payload_is_reclassified_under_same_classifier_version(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    dataset = "bid_notice_service"
    key = "CHANGE|000"

    first_sha = vnext_store.preserve_raw(
        dataset, key, {"bidNtceNm": "청사 정기 청소용역"}, source_system="G2B"
    )
    first = classification_vnext.classify_dataset(dataset)
    assert first["classified"] == 1

    second_sha = vnext_store.preserve_raw(
        dataset, key, {"bidNtceNm": "LED 가로등 교체 용역"}, source_system="G2B"
    )
    assert second_sha != first_sha
    second = classification_vnext.classify_dataset(dataset)
    assert second["classified"] == 1
    repeat = classification_vnext.classify_dataset(dataset)
    assert repeat["classified"] == 0

    with db.connect() as conn:
        row = conn.execute(
            "SELECT primary_category,source_payload_sha256 FROM classifications "
            "WHERE entity_type=? AND entity_key=? AND classifier_version=?",
            (dataset, key, vnext_schema.CLASSIFIER_VERSION),
        ).fetchone()
        current = conn.execute(
            "SELECT payload_sha256 FROM raw_records WHERE dataset=? AND source_key=?",
            (dataset, key),
        ).fetchone()
        revisions = conn.execute(
            "SELECT COUNT(*) AS n FROM raw_record_revisions WHERE dataset=? AND source_key=?",
            (dataset, key),
        ).fetchone()["n"]

    assert row["primary_category"] == "LIGHTING"
    assert row["source_payload_sha256"] == current["payload_sha256"] == second_sha
    assert revisions == 2


def test_reclassification_preserves_old_versions(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    vnext_store.preserve_raw(
        "bid_notice_goods", "A|000", {"bidNtceNm": "LED 투광등 구매"}, source_system="G2B"
    )
    first = classification_vnext.classify_dataset("bid_notice_goods", classifier_version="test-v1")
    second = classification_vnext.classify_dataset("bid_notice_goods", classifier_version="test-v2")
    repeat = classification_vnext.classify_dataset("bid_notice_goods", classifier_version="test-v2")
    assert first["classified"] == 1
    assert second["classified"] == 1
    assert repeat["classified"] == 0

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT classifier_version,primary_category FROM classifications "
            "WHERE entity_type='bid_notice_goods' AND entity_key='A|000' ORDER BY classifier_version"
        ).fetchall()
    assert [(r["classifier_version"], r["primary_category"]) for r in rows] == [
        ("test-v1", "LIGHTING"), ("test-v2", "LIGHTING")
    ]


def test_budget_postgres_classification_repairs_missing_primary_store(
    monkeypatch, tmp_path
):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_BUDGET_STORAGE", "postgresql")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        f"sqlite:///{tmp_path / 'budget-classification-pg.sqlite3'}",
    )
    monkeypatch.delenv("G2B_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRESQL_URL", raising=False)
    budget_pg_store.reset_engine_cache()

    budget_storage.preserve_raw(
        "budget",
        "incheon-led",
        {
            "fyr": "2026",
            "exe_ymd": "20261003",
            "wa_laf_hg_nm": "인천광역시",
            "laf_hg_nm": "인천광역시 연수구",
            "dept_nm": "도로과",
            "dbiz_cd": "LED-1",
            "dbiz_nm": "송도 보안등 LED 교체사업",
            "bdg_cash_amt": "100000000",
            "ep_amt": "20000000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK",
        source_date="2026-10-03",
    )

    monkeypatch.setattr(
        budget_storage,
        "current_payload_hashes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "budget classifier must not materialize all current hashes"
            )
        ),
    )

    first = classification_vnext.classify_dataset("budget", batch_size=1)

    assert first["classified"] == 1
    assert first["pending_key_materialization"] == "BOUNDED_KEYSET_BATCHES"
    assert first["classification_storage"] == (
        "POSTGRESQL_PRIMARY_PLUS_APP_COMPATIBILITY"
    )
    pg_rows = budget_pg_store.classification_rows(
        ("budget",), vnext_schema.CLASSIFIER_VERSION
    )
    assert len(pg_rows) == 1
    assert pg_rows[0]["record_key"] == "incheon-led"
    assert pg_rows[0]["primary_category"] == "LIGHTING"

    with db.connect() as conn:
        compat = conn.execute(
            """SELECT primary_category,source_payload_sha256
               FROM classifications
               WHERE entity_type='budget'
                 AND entity_key='incheon-led'
                 AND classifier_version=?""",
            (vnext_schema.CLASSIFIER_VERSION,),
        ).fetchone()
    assert compat["primary_category"] == "LIGHTING"

    # Reproduce 4.1.139: compatibility classification exists but the PostgreSQL
    # budget classification table is empty. The next source-free classify pass
    # must repair PostgreSQL instead of trusting the compatibility row.
    engine, tables = budget_pg_store._engine_and_tables()
    with engine.begin() as conn:
        conn.execute(tables["classifications"].delete())

    assert budget_pg_store.classification_rows(
        ("budget",), vnext_schema.CLASSIFIER_VERSION
    ) == []

    repaired = classification_vnext.classify_dataset("budget")

    assert repaired["classified"] == 1
    repaired_rows = budget_pg_store.classification_rows(
        ("budget",), vnext_schema.CLASSIFIER_VERSION
    )
    assert len(repaired_rows) == 1
    assert repaired_rows[0]["primary_category"] == "LIGHTING"


def test_budget_postgres_category_filter_sees_repaired_classification(
    monkeypatch, tmp_path
):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_BUDGET_STORAGE", "postgresql")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        f"sqlite:///{tmp_path / 'budget-screen-pg.sqlite3'}",
    )
    monkeypatch.delenv("G2B_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRESQL_URL", raising=False)
    budget_pg_store.reset_engine_cache()

    budget_storage.preserve_raw(
        "budget",
        "incheon-lighting-screen",
        {
            "fyr": "2026",
            "exe_ymd": "20261003",
            "wa_laf_hg_nm": "인천광역시",
            "laf_hg_nm": "인천광역시 남동구",
            "dbiz_cd": "L-2",
            "dbiz_nm": "가로등 신규 설치 및 유지보수",
            "bdg_cash_amt": "500000000",
            "ep_amt": "100000000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK",
        source_date="2026-10-03",
    )
    classification_vnext.classify_dataset("budget")

    rows = budget_pg_store.current_project_rows(
        ["budget"],
        fiscal_year=2026,
        source_layers=("DETAIL_EXECUTION",),
        categories=("LIGHTING",),
        classifier_version=vnext_schema.CLASSIFIER_VERSION,
        region_terms=("인천광역시", "인천"),
        limit=20,
    )

    assert [row["record_key"] for row in rows] == [
        "incheon-lighting-screen"
    ]
    assert rows[0]["primary_category"] == "LIGHTING"


def test_schema_migrates_old_classifications_table_additively(monkeypatch, tmp_path):
    path = tmp_path / "old-vnext.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    with db.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS classifications")
        conn.execute(
            """CREATE TABLE classifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                primary_category TEXT NOT NULL DEFAULT 'UNCLASSIFIED',
                subcategory TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                classifier_version TEXT NOT NULL,
                classified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(entity_type, entity_key, classifier_version)
            )"""
        )
        conn.execute(
            "INSERT INTO classifications(entity_type,entity_key,primary_category,classifier_version) "
            "VALUES('bid_notice_goods','A|000','OTHER','old-v1')"
        )
        vnext_schema.ensure_vnext_schema(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(classifications)").fetchall()}
        old = conn.execute(
            "SELECT primary_category,source_payload_sha256 FROM classifications WHERE entity_key='A|000'"
        ).fetchone()
    assert "source_payload_sha256" in cols
    assert old["primary_category"] == "OTHER"
    assert old["source_payload_sha256"] == ""


def test_schema_refreshes_classifier_version_setting(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES('classifier_version','old') "
            "ON CONFLICT(key) DO UPDATE SET value='old'"
        )
        vnext_schema.ensure_vnext_schema(conn)
        value = conn.execute(
            "SELECT value FROM app_settings WHERE key='classifier_version'"
        ).fetchone()["value"]
    assert value == vnext_schema.CLASSIFIER_VERSION == "1.1.0-rule-v1"



def test_classify_dataset_reuses_one_write_connection_per_batch(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    for index in range(3):
        vnext_store.preserve_raw(
            "bid_notice_service",
            f"BATCH|{index}",
            {"bidNtceNm": f"LED 조명 {index}"},
            source_system="G2B",
        )

    original = classification_vnext.save_classification
    seen_connections = []

    def wrapped(*args, **kwargs):
        conn = kwargs.get("_conn")
        assert conn is not None
        seen_connections.append(conn)
        return original(*args, **kwargs)

    monkeypatch.setattr(classification_vnext, "save_classification", wrapped)
    report = classification_vnext.classify_dataset(
        "bid_notice_service",
        batch_size=2,
    )

    assert report["classified"] == 3
    assert len(seen_connections) == 3
    assert seen_connections[0] is seen_connections[1]
    assert seen_connections[2] is not seen_connections[1]


def test_budget_classification_writes_primary_and_compat_on_one_pg_checkout(monkeypatch):
    """Budget and app writes must fit the worker's 1+1 PostgreSQL pool."""
    from contextlib import contextmanager

    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(budget_pg_store, "current_state_count", lambda _name: 1)
    monkeypatch.setattr(
        budget_pg_store, "pending_classification_key_batches",
        lambda *_args, **_kwargs: iter([["test-key"]]),
    )
    monkeypatch.setattr(
        budget_storage, "current_raw_for_keys",
        lambda *_args, **_kwargs: iter([[
            {"source_key": "test-key", "payload_sha256": "digest",
             "payload": {"dbiz_nm": "LED 보안등 교체사업"}}
        ]]),
    )
    monkeypatch.setattr(classification_vnext, "_memory_checkpoint", lambda: None)
    monkeypatch.setattr(classification_vnext, "ensure_vnext_schema", lambda _conn: None)

    class GuardEngine:
        def begin(self):
            raise AssertionError("third PostgreSQL connection checkout")

    monkeypatch.setattr(budget_pg_store, "_engine_and_tables", lambda: (GuardEngine(), {}))

    class AppSession:
        def __init__(self):
            self._conn = object()
        def execute(self, statement):
            assert statement == "BEGIN IMMEDIATE"

    app_session = AppSession()

    @contextmanager
    def fake_connect():
        yield app_session

    monkeypatch.setattr(classification_vnext, "connect", fake_connect)
    primary = []
    compat = []
    monkeypatch.setattr(
        budget_pg_store, "save_classification",
        lambda *_args, **kw: primary.append(kw["_conn"]),
    )
    monkeypatch.setattr(
        classification_vnext, "save_compat_classification",
        lambda *_args, **kw: compat.append(kw["_conn"]),
    )
    result = classification_vnext.classify_dataset("budget", batch_size=1, max_batches=1)
    assert result["classified"] == 1
    assert primary == [app_session._conn]
    assert compat == [app_session]


def test_classify_dataset_respects_max_batches(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    calls = []
    rows = [
        {"id": 1, "source_key": "A", "payload_json": "{}", "payload_sha256": "a"},
        {"id": 2, "source_key": "B", "payload_json": "{}", "payload_sha256": "b"},
    ]

    def fake_batch(dataset, version, last_id, size, force=False):
        calls.append(last_id)
        if last_id == 0:
            return [rows[0]]
        if last_id == 1:
            return [rows[1]]
        return []

    monkeypatch.setattr(classification_vnext, "_batch_rows", fake_batch)
    monkeypatch.setattr(
        classification_vnext,
        "save_classification",
        lambda *args, **kwargs: None,
    )

    result = classification_vnext.classify_dataset(
        "synthetic",
        batch_size=1,
        max_batches=1,
    )

    assert result["classified"] == 1
    assert result["batches_done"] == 1
    assert result["batch_limit_reached"] is True
    assert calls == [0]
