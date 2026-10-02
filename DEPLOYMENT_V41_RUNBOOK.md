# G2B vNext 4.1 Cafe24 Release Runbook

This runbook is the deployment handoff for **SINSUNG G2B VNEXT 4.1.10**.

4.1 is a storage-contract reset, not an in-place 4.0 data migration. The owner
approved discarding the existing G2B 4.0 dataset and rebuilding it from official
sources.

## 1. 4.1 storage contract

Production has one PostgreSQL database.

- `g2b_app`
  - administrator/session state
  - application settings and source credentials
  - normalized 2026-09-01+ lighting/pole business records
  - collection checkpoints and page receipts
  - lightweight read models
- `g2b_budget`
  - normalized QWGJK/AIDFA budget current state
  - bounded normalized budget revisions
  - budget checkpoints/page receipts and classifications/projections
- `g2b_meta`
  - release bootstrap marker only

SQLite is not a production dependency in 4.1. It remains available only when
`G2B_TEST_MODE=1` for isolated regression tests.

## 2. Scope remains narrow

Allowed operational source domains:

- shopping delivery requests from 2026-09-01 forward
- only lighting/pole target detail rows are stored from shopping
- next-fiscal-year AIDFA appropriation is checked before current-year QWGJK
- completed future AIDFA scopes are rechecked on a newer date so early 0-row results do not become permanent
- QWGJK current state remains on the newest operational snapshot while historical snapshots are backfilled from 2026-01-01 through D-1 using remaining LOFIN quota
- historical QWGJK snapshots use a separate `history:year:date` checkpoint namespace, persist normalized observation/revision history only, and do not move current state backwards
- QWGJK budget collection is normalized on receipt; source JSON is not persisted

Still delegated / blocked:

- goods bid notices -> NO1
- service notices -> NO1
- opening results -> NO1
- awards -> NO1
- contracts -> NO1
- bulk historical -> HOLD
- APPROVED_HISTORICAL -> HOLD
- education live transport -> HOLD

4.1 changes storage, not this source boundary.

## 3. First 4.1 deployment

Use the Cafe24 PostgreSQL service that belongs to the G2B project.

Required:

```text
G2B_TEST_MODE=0
G2B_AUTO_SYNC=0
G2B_RUNTIME_ROLE=UNIFIED
G2B_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DBNAME
G2B_APP_SCHEMA=g2b_app
G2B_BUDGET_SCHEMA=g2b_budget
G2B_V41_FRESH_START=1

# The two workload schemas must be distinct and neither may be g2b_meta.
```

Source credential contract:
- `G2B_SERVICE_KEY`: shopping delivery requests
- `LOFIN_API_KEY`: current QWGJK + next-year AIDFA budget reads
- `EDUINFO_API_KEY`: stored credential only; live transport remains HOLD

`G2B_DATABASE_URL` is canonical. The runtime can also discover a unique Cafe24
PostgreSQL connection from DB_*, PG*, POSTGRES_URL/POSTGRESQL_URL/DATABASE_URL.
Production does not accept `G2B_BUDGET_DATABASE_URL`; that name is reserved only
for isolated test-mode SQLite fixtures.

Do not configure `G2B_DB_PATH`, `G2B_SQLITE_WAL`, or other SQLite settings in
production.

## 4. One-time fresh start

On the first 4.1 boot:

1. acquire PostgreSQL advisory transaction lock `g2b_v41_fresh_start`
2. inspect `g2b_meta.release_bootstrap`
3. if prior G2B storage exists and `G2B_V41_FRESH_START=1` is absent, fail closed
4. drop only the G2B-owned `g2b_app` and `g2b_budget` schemas
5. recreate empty workload schemas
6. write marker `fresh_start_4_1_0=NORMALIZED_NO_RAW_V1`
7. delete the legacy G2B SQLite file on a best-effort basis
8. normal schema installers create the 4.1 tables

The versioned marker makes subsequent restarts idempotent even if the environment variable
has not yet been removed.

After the normalized/no-RAW fresh-start succeeds, **remove `G2B_V41_FRESH_START`**.

## 5. Shared PostgreSQL pool

Recommended defaults:

```text
G2B_DB_POOL_SIZE=5
G2B_DB_MAX_OVERFLOW=2
G2B_DB_POOL_TIMEOUT_SECONDS=5
G2B_DB_POOL_RECYCLE_SECONDS=900
G2B_DB_CONNECT_TIMEOUT_SECONDS=3
G2B_DB_LOCK_TIMEOUT_MS=5000
G2B_DB_STATEMENT_TIMEOUT_MS=120000
G2B_FUTURE_BUDGET_SYNC_MAX_PAGES=24
LOFIN_VNEXT_API_DAILY_LIMIT=100
```

Control/shopping and budget code share the same SQLAlchemy PostgreSQL pool. This
avoids the 4.0 pattern of independent application and budget connection pools.

## 6. First boot acceptance

Keep:

```text
G2B_AUTO_SYNC=0
```

Verify in order:

1. `/live` -> HTTP 200
2. `/health` -> HTTP 200
3. `/ready` -> HTTP 200
4. source-free preflight
5. key-aware preflight
6. bounded source canary on disposable storage: shopping + QWGJK + next-year AIDFA
7. one-page QWGJK deployment canary on production PostgreSQL
8. checkpoint/resume verification
9. keep `G2B_AUTO_SYNC=0`; enable automatic collection only after separate owner approval

A database outage must not collapse `/live` or `/health` to a platform 502.
It must make `/ready` return 503.

## 7. Readiness contract

Production UNIFIED readiness requires:

```text
backend_ok=true
db_persistent=true
storage_backend=POSTGRESQL_UNIFIED
budget_postgres_configured=true
budget_postgres_ready=true
operational_ready=true
```

When the PostgreSQL connection is missing, the required boot variable is
`G2B_DATABASE_URL`.

## 8. Collection safety

The existing safety model remains:

- one worker thread per Python process
- one PostgreSQL advisory operational-cycle lease across processes
- transactional normalized record + page receipt + next-page checkpoint
- bounded next-year AIDFA canary is read-only and production-DB isolated
- production QWGJK canary page 1 -> resume page 2
- same-day COMPLETE checkpoint -> no duplicate QWGJK fetch
- unresolved QWGJK snapshots survive the short receipt window and are resumed before opening a newer snapshot
- RUNNING/FAILED/INCOMPLETE active-generation receipts are protected up to the long retention boundary
- a newer COMPLETE snapshot in the same fiscal year marks older unresolved nationwide checkpoints SUPERSEDED

The advisory operational lease name is `g2b_v41_operational_cycle`.

## 9. Data policy

The 4.1 reset intentionally discards the pre-4.1 G2B dataset.

- Source JSON is transient and is not persisted in production.
- Budget history is rolling 365 days, with QWGJK historical backfill starting at 2026-01-01.
- A non-empty COMPLETE nationwide QWGJK/AIDFA snapshot reconciles current state: records absent from that generation are removed from current/read state while revision history is retained.
- A zero-row COMPLETE snapshot is fail-safe and does not wipe all existing current state.
- Current rows for future fiscal years are protected from age-based expiry.
- Non-budget business records start at 2026-09-01.
- Shopping delivery history is backfilled from 2026-09-01 forward; older non-budget material is not backfilled.
- Goods/service bid and award/contract domains remain delegated to NO1.

## 10. Long-term scaling rule

Keep one PostgreSQL database and the current CONTROL/BUDGET/READ responsibilities
until measured workload requires otherwise. Because 4.1 does not retain source JSON,
there is no planned RAW data-lake tier.

The operational collector reads the remaining LOFIN daily allowance before each
budget cycle. Future AIDFA receives priority, and current QWGJK is capped to the
remaining permits instead of intentionally running into the local quota exception.

Do not raise `LOFIN_VNEXT_API_DAILY_LIMIT` until the official source allowance is
confirmed.

Do not add Kafka, Redis, object storage, or separate worker infrastructure until
measured load requires it.
