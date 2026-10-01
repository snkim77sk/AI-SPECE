# G2B vNext 4.1 Cafe24 Release Runbook

This runbook is the deployment handoff for **SINSUNG G2B VNEXT 4.1.0**.

4.1 is a storage-contract reset, not an in-place 4.0 data migration. The owner
approved discarding the existing G2B 4.0 dataset and rebuilding it from official
sources.

## 1. 4.1 storage contract

Production has one PostgreSQL database.

- `g2b_app`
  - administrator/session state
  - application settings and source credentials
  - shopping RAW/current revision history
  - collection checkpoints and page receipts
  - classifications and lightweight read models
- `g2b_budget`
  - QWGJK/AIDFA budget RAW/current state
  - budget checkpoints/page receipts
  - budget classifications/projections
- `g2b_meta`
  - release bootstrap marker only

SQLite is not a production dependency in 4.1. It remains available only when
`G2B_TEST_MODE=1` for isolated regression tests.

## 2. Scope remains narrow

Allowed operational source domains:

- shopping delivery requests from 2026-10-01 forward
- only lighting/pole target detail rows are stored from shopping
- QWGJK current-fiscal-year full budget RAW

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
```

`G2B_DATABASE_URL` is canonical. The runtime can also discover a unique Cafe24
PostgreSQL connection from DB_*, PG*, POSTGRES_URL/POSTGRESQL_URL/DATABASE_URL.
An old `G2B_BUDGET_DATABASE_URL` is accepted only as a one-release compatibility
alias and should be removed after the 4.1 deployment.

Do not configure `G2B_DB_PATH`, `G2B_SQLITE_WAL`, or other SQLite settings in
production.

## 4. One-time fresh start

On the first 4.1 boot:

1. acquire PostgreSQL advisory transaction lock `g2b_v41_fresh_start`
2. inspect `g2b_meta.release_bootstrap`
3. if prior G2B storage exists and `G2B_V41_FRESH_START=1` is absent, fail closed
4. drop only the G2B-owned `g2b_app` and `g2b_budget` schemas
5. recreate empty workload schemas
6. write marker `fresh_start_4_1_0=COMPLETE`
7. delete the legacy G2B SQLite file on a best-effort basis
8. normal schema installers create the 4.1 tables

The marker makes subsequent restarts idempotent even if the environment variable
has not yet been removed.

After the first successful deployment, **remove `G2B_V41_FRESH_START`**.

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
6. one-page QWGJK live canary
7. checkpoint/resume verification
8. only then set `G2B_AUTO_SYNC=1`

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
- transactional RAW + page receipt + next-page checkpoint
- canary page 1 -> resume page 2
- same-day COMPLETE checkpoint -> no duplicate QWGJK fetch

The advisory operational lease name is `g2b_v41_operational_cycle`.

## 9. Data policy

The 4.1 reset intentionally discards the pre-4.1 G2B dataset. Recollection starts
from the current approved operational scope.

Do not re-enable old bid/service/opening/award/contract collection as part of the
storage reset. Those domains remain delegated to NO1.

## 10. Long-term scaling rule

PostgreSQL remains the correct default while the retained RAW volume is moderate.
If immutable RAW grows to tens/hundreds of GB and retention requirements expand,
move cold immutable payload bodies to object storage while keeping identity,
hashes, current state, checkpoints, and searchable projections in PostgreSQL.

Do not add Kafka, Redis, or separate worker infrastructure until measured load
requires them.
