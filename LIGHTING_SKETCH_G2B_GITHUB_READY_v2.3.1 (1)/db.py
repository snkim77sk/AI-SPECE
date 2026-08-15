import os
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _resolve_db_path():
    configured = str(os.getenv('G2B_DB_PATH', '') or '').strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    # AI SPACE persistent storage. If it is mounted, prefer it automatically.
    persistent_dir = '/app/user_data'
    if os.path.isdir(persistent_dir) and os.access(persistent_dir, os.W_OK):
        return os.path.join(persistent_dir, 'g2b.sqlite3')
    return os.path.join(BASE_DIR, 'data', 'g2b.sqlite3')

DB_PATH = _resolve_db_path()

SCHEMA = r'''
CREATE TABLE IF NOT EXISTS shopping_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base_date TEXT NOT NULL,
    contract_date TEXT NOT NULL DEFAULT '',
    delivery_req_date TEXT NOT NULL DEFAULT '',
    final_yn TEXT NOT NULL DEFAULT '',
    demand_org TEXT NOT NULL DEFAULT '',
    demand_region TEXT NOT NULL DEFAULT '',
    top_org TEXT NOT NULL DEFAULT '',
    contract_name TEXT NOT NULL DEFAULT '',
    contract_method TEXT NOT NULL DEFAULT '',
    delivery_deadline TEXT NOT NULL DEFAULT '',
    detail_item_no TEXT NOT NULL DEFAULT '',
    detail_item_name TEXT NOT NULL DEFAULT '',
    item_id TEXT NOT NULL DEFAULT '',
    item_name TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    unit_price INTEGER NOT NULL DEFAULT 0,
    quantity REAL NOT NULL DEFAULT 0,
    supply_amount INTEGER NOT NULL DEFAULT 0,
    vendor_name TEXT NOT NULL DEFAULT '',
    vendor_bizno TEXT NOT NULL DEFAULT '',
    contract_no TEXT NOT NULL DEFAULT '',
    delivery_req_no TEXT NOT NULL DEFAULT '',
    delivery_req_detail_seq TEXT NOT NULL DEFAULT '',
    source_key TEXT NOT NULL DEFAULT '',
    is_sample INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_shopping_source_key
    ON shopping_contracts(source_key) WHERE source_key <> '';
CREATE INDEX IF NOT EXISTS ix_shopping_date ON shopping_contracts(base_date);
CREATE INDEX IF NOT EXISTS ix_shopping_vendor ON shopping_contracts(vendor_name);
CREATE INDEX IF NOT EXISTS ix_shopping_org ON shopping_contracts(demand_org);
CREATE INDEX IF NOT EXISTS ix_shopping_region ON shopping_contracts(demand_region);
CREATE INDEX IF NOT EXISTS ix_shopping_item_id ON shopping_contracts(item_id);
CREATE INDEX IF NOT EXISTS ix_shopping_detail_item ON shopping_contracts(detail_item_name);

CREATE TABLE IF NOT EXISTS bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_no TEXT NOT NULL DEFAULT '',
    notice_order TEXT NOT NULL DEFAULT '',
    notice_date TEXT NOT NULL DEFAULT '',
    close_date TEXT NOT NULL DEFAULT '',
    open_date TEXT NOT NULL DEFAULT '',
    notice_name TEXT NOT NULL DEFAULT '',
    notice_org TEXT NOT NULL DEFAULT '',
    demand_org TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    business_type TEXT NOT NULL DEFAULT '',
    method_name TEXT NOT NULL DEFAULT '',
    budget_amount INTEGER NOT NULL DEFAULT 0,
    estimated_price INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL DEFAULT '',
    source_key TEXT NOT NULL DEFAULT '',
    is_sample INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bids_source_key ON bids(source_key) WHERE source_key <> '';
CREATE INDEX IF NOT EXISTS ix_bids_notice_date ON bids(notice_date);
CREATE INDEX IF NOT EXISTS ix_bids_notice_org ON bids(notice_org);
CREATE INDEX IF NOT EXISTS ix_bids_demand_org ON bids(demand_org);

CREATE TABLE IF NOT EXISTS budget_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fiscal_year INTEGER NOT NULL DEFAULT 0,
    region TEXT NOT NULL DEFAULT '',
    org_name TEXT NOT NULL DEFAULT '',
    project_name TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    budget_amount INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    is_sample INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_budget_year ON budget_items(fiscal_year);
CREATE INDEX IF NOT EXISTS ix_budget_org ON budget_items(org_name);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sync_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_type TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT NOT NULL DEFAULT '',
    range_start TEXT NOT NULL DEFAULT '',
    range_end TEXT NOT NULL DEFAULT '',
    processed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT ''
);
'''

DEFAULTS = {
    'company_name': os.getenv('COMPANY_NAME', '주식회사 라이팅스케치'),
    'default_region': os.getenv('G2B_DEFAULT_REGION', '인천광역시'),
    'company_aliases': os.getenv('G2B_COMPANY_ALIASES', ''),
    'api_key': os.getenv('G2B_SERVICE_KEY', ''),
    'shop_api_base_url': os.getenv('G2B_SHOP_BASE_URL', 'https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService'),
    'shop_api_operation': os.getenv('G2B_SHOP_DETAIL_OPERATION', 'getDlvrReqDtlInfoList'),
    'bid_api_base_url': os.getenv('G2B_BID_BASE_URL', 'https://apis.data.go.kr/1230000/ad/BidPublicInfoService'),
    'auto_sync_enabled': os.getenv('G2B_AUTO_SYNC', '0'),
    'auto_sync_hours': os.getenv('G2B_AUTO_SYNC_HOURS', '3'),
    'auto_sync_days': os.getenv('G2B_AUTO_SYNC_DAYS', '14'),
    'api_daily_limit': os.getenv('G2B_API_DAILY_LIMIT', '900'),
    'last_sync': '',
    'last_sync_result': '아직 실데이터 동기화를 실행하지 않았습니다.',
    'last_shop_raw_count': '0',
    'last_shop_matched_count': '0',
    'last_shop_saved_count': '0',
    'last_shop_skipped_count': '0',
    'last_shop_first_fields': '',
    'last_shop_error': '',
    'last_bid_sync': '',
    'last_bid_sync_result': '아직 입찰공고 동기화를 실행하지 않았습니다.',
    'last_service_sync': '',
    'last_service_sync_result': '아직 용역공고 동기화를 실행하지 않았습니다.',
    'api_calls_shop_date': '',
    'api_calls_shop_count': '0',
    'api_calls_bid_date': '',
    'api_calls_bid_count': '0',
    'last_auto_sync': '',
    'backfill_status': '대기',
    'backfill_progress': '0',
    'backfill_message': '',
    'backfill_cursor': '',
    'backfill_total_saved': '0',
}

@contextmanager
def connect():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _columns(conn, table):
    return {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}


def _migrate(conn):
    cols = _columns(conn, 'shopping_contracts')
    additions = {
        'top_org': "TEXT NOT NULL DEFAULT ''", 'is_sample': 'INTEGER NOT NULL DEFAULT 0',
        'contract_date': "TEXT NOT NULL DEFAULT ''", 'delivery_req_date': "TEXT NOT NULL DEFAULT ''",
        'final_yn': "TEXT NOT NULL DEFAULT ''", 'contract_method': "TEXT NOT NULL DEFAULT ''",
        'delivery_deadline': "TEXT NOT NULL DEFAULT ''", 'detail_item_no': "TEXT NOT NULL DEFAULT ''",
        'unit': "TEXT NOT NULL DEFAULT ''", 'delivery_req_detail_seq': "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in cols:
            conn.execute(f'ALTER TABLE shopping_contracts ADD COLUMN {name} {definition}')
    conn.execute("UPDATE shopping_contracts SET top_org=demand_org WHERE top_org='' OR top_org IS NULL")


def init_db():
    with connect() as conn:
        # SQLite 운영 안정화: 읽기/쓰기 동시성 개선. AI SPACE 무료체험/소규모 운영용.
        try:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
        except sqlite3.DatabaseError:
            pass
        conn.executescript(SCHEMA)
        _migrate(conn)
        for k, v in DEFAULTS.items():
            conn.execute('INSERT OR IGNORE INTO app_settings(key,value) VALUES (?,?)', (k, str(v)))


ENV_OVERRIDES = {
    'company_name': 'COMPANY_NAME',
    'default_region': 'G2B_DEFAULT_REGION',
    'company_aliases': 'G2B_COMPANY_ALIASES',
    'api_key': 'G2B_SERVICE_KEY',
    'shop_api_base_url': 'G2B_SHOP_BASE_URL',
    'shop_api_operation': 'G2B_SHOP_DETAIL_OPERATION',
    'bid_api_base_url': 'G2B_BID_BASE_URL',
    'auto_sync_enabled': 'G2B_AUTO_SYNC',
    'auto_sync_hours': 'G2B_AUTO_SYNC_HOURS',
    'auto_sync_days': 'G2B_AUTO_SYNC_DAYS',
    'api_daily_limit': 'G2B_API_DAILY_LIMIT',
}

def get_setting(key, default=''):
    # Cloud/VPS deployments keep secrets and core runtime settings in environment
    # variables. Environment values take precedence over values stored in SQLite.
    env_name = ENV_OVERRIDES.get(key)
    if env_name and os.getenv(env_name) not in (None, ''):
        return os.getenv(env_name)
    with connect() as conn:
        row = conn.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default


def set_setting(key, value):
    with connect() as conn:
        conn.execute('INSERT INTO app_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


def settings_dict():
    with connect() as conn:
        return {r['key']: r['value'] for r in conn.execute('SELECT key,value FROM app_settings')}


def new_sync_log(sync_type, start='', end=''):
    with connect() as conn:
        cur = conn.execute(
            'INSERT INTO sync_logs(sync_type,range_start,range_end,status,message) VALUES (?,?,?,?,?)',
            (sync_type, start, end, 'RUNNING', '')
        )
        return cur.lastrowid


def finish_sync_log(log_id, status, processed=0, message=''):
    with connect() as conn:
        conn.execute(
            "UPDATE sync_logs SET finished_at=CURRENT_TIMESTAMP,status=?,processed=?,message=? WHERE id=?",
            (status, int(processed or 0), str(message), log_id)
        )

