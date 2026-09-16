"""G2B vNext data-foundation schema.

This module is intentionally additive. Existing dashboard tables remain the
serving layer while vNext collectors can preserve complete source payloads,
classify them repeatedly, and link procurement lifecycle records without
re-fetching historical source data.
"""

CLASSIFIER_VERSION = "1.1.0-rule-v1"

VNEXT_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS raw_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset TEXT NOT NULL,
    source_system TEXT NOT NULL DEFAULT '',
    source_operation TEXT NOT NULL DEFAULT '',
    source_key TEXT NOT NULL,
    source_date TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL DEFAULT '',
    normalized_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dataset, source_key)
);
CREATE INDEX IF NOT EXISTS ix_raw_records_dataset_date
    ON raw_records(dataset, source_date);
CREATE INDEX IF NOT EXISTS ix_raw_records_fetched
    ON raw_records(fetched_at);

CREATE TABLE IF NOT EXISTS raw_record_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset TEXT NOT NULL,
    source_system TEXT NOT NULL DEFAULT '',
    source_operation TEXT NOT NULL DEFAULT '',
    source_key TEXT NOT NULL,
    source_date TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    UNIQUE(dataset, source_key, payload_sha256)
);
CREATE INDEX IF NOT EXISTS ix_raw_revisions_source
    ON raw_record_revisions(dataset, source_key, id);
CREATE INDEX IF NOT EXISTS ix_raw_revisions_fetched
    ON raw_record_revisions(fetched_at);

CREATE TABLE IF NOT EXISTS classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    primary_category TEXT NOT NULL DEFAULT 'UNCLASSIFIED',
    subcategory TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    classifier_version TEXT NOT NULL,
    source_payload_sha256 TEXT NOT NULL DEFAULT '',
    classified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_key, classifier_version)
);
CREATE INDEX IF NOT EXISTS ix_classifications_category
    ON classifications(entity_type, primary_category);

CREATE TABLE IF NOT EXISTS collection_checkpoints (
    dataset TEXT NOT NULL,
    scope_key TEXT NOT NULL DEFAULT 'default',
    cursor_value TEXT NOT NULL DEFAULT '',
    range_start TEXT NOT NULL DEFAULT '',
    range_end TEXT NOT NULL DEFAULT '',
    page_no INTEGER NOT NULL DEFAULT 0,
    source_total INTEGER NOT NULL DEFAULT 0,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    saved_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'IDLE',
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(dataset, scope_key)
);

CREATE TABLE IF NOT EXISTS lifecycle_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_type TEXT NOT NULL,
    from_key TEXT NOT NULL,
    to_type TEXT NOT NULL,
    to_key TEXT NOT NULL,
    link_type TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(from_type, from_key, to_type, to_key, link_type)
);
CREATE INDEX IF NOT EXISTS ix_lifecycle_from
    ON lifecycle_links(from_type, from_key);
CREATE INDEX IF NOT EXISTS ix_lifecycle_to
    ON lifecycle_links(to_type, to_key);

CREATE TABLE IF NOT EXISTS award_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_no TEXT NOT NULL DEFAULT '',
    notice_order TEXT NOT NULL DEFAULT '',
    business_type TEXT NOT NULL DEFAULT '',
    opening_date TEXT NOT NULL DEFAULT '',
    participant_count INTEGER NOT NULL DEFAULT 0,
    first_rank_vendor TEXT NOT NULL DEFAULT '',
    first_rank_bizno TEXT NOT NULL DEFAULT '',
    first_rank_amount INTEGER NOT NULL DEFAULT 0,
    final_vendor TEXT NOT NULL DEFAULT '',
    final_vendor_bizno TEXT NOT NULL DEFAULT '',
    final_award_amount INTEGER NOT NULL DEFAULT 0,
    award_rate REAL NOT NULL DEFAULT 0,
    contract_no TEXT NOT NULL DEFAULT '',
    contract_vendor TEXT NOT NULL DEFAULT '',
    contract_vendor_bizno TEXT NOT NULL DEFAULT '',
    contract_amount INTEGER NOT NULL DEFAULT 0,
    source_key TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_award_results_source
    ON award_results(source_key) WHERE source_key <> '';
CREATE INDEX IF NOT EXISTS ix_award_notice
    ON award_results(notice_no, notice_order);
CREATE INDEX IF NOT EXISTS ix_award_final_vendor
    ON award_results(final_vendor);
'''


def ensure_vnext_schema(conn):
    """Install additive vNext tables/migrations on an existing G2B SQLite connection."""
    conn.executescript(VNEXT_SCHEMA)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(classifications)").fetchall()}
    if "source_payload_sha256" not in cols:
        conn.execute("ALTER TABLE classifications ADD COLUMN source_payload_sha256 TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_classifications_payload "
        "ON classifications(entity_type, entity_key, classifier_version, source_payload_sha256)"
    )
    conn.execute(
        "INSERT INTO app_settings(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("classifier_version", CLASSIFIER_VERSION),
    )