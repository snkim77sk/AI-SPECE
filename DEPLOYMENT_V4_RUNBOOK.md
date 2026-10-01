# G2B vNext 4.0 Cafe24 Release Runbook

This runbook is the deployment handoff for `SINSUNG G2B VNEXT 4.0.0`.

It does **not** authorize bulk historical collection, service/bid/award/contract
collection, or education live transport. Those remain outside the G2B 4.0 scope.

## 1. Fixed release boundary

Allowed operational source domains:

- shopping delivery requests, source scan from 2026-09-01 forward
- only lighting/pole detail items are stored from shopping
- QWGJK budget current-fiscal-year full RAW in PostgreSQL

Blocked / delegated:

- goods bid notices -> NO1
- service notices -> NO1
- opening results -> NO1
- awards -> NO1
- contracts -> NO1
- bulk historical -> HOLD
- APPROVED_HISTORICAL -> HOLD
- education live transport -> HOLD

## 2. First deployment environment

Use `.env.example` as the canonical Cafe24 input template. Copy the values into
Cafe24 environment settings; do not upload the file with real secrets and never
commit a populated `.env`.

Start with source automation disabled.

```text
G2B_TEST_MODE=0
G2B_AUTO_SYNC=0
G2B_RUNTIME_ROLE=UNIFIED
G2B_BUDGET_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DBNAME
```

`G2B_RUNTIME_ROLE` may be omitted because UNIFIED is the default, but setting it
explicitly on the first deployment is recommended.

Cafe24 persistent storage must expose:

```text
/app/user_data
```

Do not set `G2B_DB_PATH` unless there is a deliberate rollback/compatibility reason.
The normal control database is:

```text
/app/user_data/g2b-vnext.sqlite3
```

Source credentials may be environment variables or saved from the authenticated
`/settings` page:

```text
G2B_SERVICE_KEY
LOFIN_API_KEY
```

`EDUINFO_API_KEY` may be stored, but education live transport remains HOLD.

## 3. Recommended defaults

```text
G2B_SHOPPING_SYNC_INTERVAL_SECONDS=7200
G2B_SHOPPING_SYNC_DAYS_PER_RUN=31
G2B_BUDGET_SYNC_MAX_PAGES=256
G2B_BUDGET_SYNC_MAX_REQUESTS=320
G2B_OPERATIONAL_LEASE_RETRY_SECONDS=15

G2B_BUDGET_SCHEMA=g2b_budget
G2B_BUDGET_POOL_SIZE=3
G2B_BUDGET_MAX_OVERFLOW=1
G2B_BUDGET_POOL_TIMEOUT_SECONDS=5
G2B_BUDGET_POOL_RECYCLE_SECONDS=900
G2B_BUDGET_CONNECT_TIMEOUT_SECONDS=3
G2B_BUDGET_LOCK_TIMEOUT_MS=5000
G2B_BUDGET_STATEMENT_TIMEOUT_MS=120000
G2B_BUDGET_RETENTION_BATCH_SIZE=5000
G2B_BUDGET_RETENTION_DAYS=365
G2B_BUDGET_RECEIPT_RETENTION_DAYS=3

G2B_SQLITE_TIMEOUT=3
G2B_SQLITE_WAL=0
```

## 4. PostgreSQL role contract

If the application creates the schema/tables itself, the bootstrap role needs the
required DDL rights.

For a pre-provisioned schema, the application role can be restricted to:

- CONNECT on the database
- USAGE on the `g2b_budget` schema
- SELECT / INSERT / UPDATE / DELETE on all declared budget tables
- PostgreSQL advisory lock usage
- existing PK / UNIQUE / declared indexes must already satisfy the 4.0 contract

The CI release gate verifies this restricted-role path on PostgreSQL 16.

## 5. First boot with AUTO_SYNC off

Deploy with:

```text
G2B_AUTO_SYNC=0
```

Expected endpoints:

- `/live` -> HTTP 200, `process_alive=true`
- `/health` -> HTTP 200
- `/ready` -> HTTP 200 only when persistent SQLite and budget PostgreSQL are ready

A PostgreSQL outage must not take `/live` or `/health` down. It should make
`/ready` return 503.

## 6. Source-free preflight

Run from repository root:

```bash
python scripts/g2b_deployment_preflight.py
```

Required before any live canary:

```text
source_io_performed=false
runtime_role=UNIFIED
test_mode_enabled=false
control_storage_ready=true
control_storage_persistent=true
budget_backend=POSTGRESQL
budget_postgres_configured=true
budget_postgres_ready=true
infrastructure_ready=true
collection_keys_ready=true
collection_ready=true
required_actions=[]
```

Important: the script exit code is based on `infrastructure_ready`. For live source
work, also require `collection_keys_ready=true` and `collection_ready=true`.

The output must never contain the database URL, database password, or API-key values.

## 7. One-page live QWGJK canary

Keep the web deployment itself on:

```text
G2B_AUTO_SYNC=0
```

Then run:

```bash
python scripts/g2b_budget_deployment_canary.py --allow-live
```

Hard safety limits in code:

- current KST date only
- QWGJK only
- page_size = 1000
- max_pages = 1
- explicit `--allow-live` required
- PostgreSQL required
- LOFIN key required
- AUTO_SYNC must already be disabled
- no shopping/service/opening/award/contract/AIDFA/education source call from this command

Valid canary outcomes:

1. Source contains more than one page:
   - collector status `RUNNING`
   - checkpoint `page_no=2`
   - `resume_expected=true`

2. Source fits in one page:
   - collector/checkpoint may already be `COMPLETE`
   - no second page is required

Neither outcome proves whole-source historical completeness.

## 8. Resume and automatic collection

After the canary result and PostgreSQL checkpoint are confirmed:

1. keep the same PostgreSQL database/schema
2. restart the Cafe24 process
3. change `G2B_AUTO_SYNC=1`
4. keep normal `G2B_BUDGET_SYNC_MAX_PAGES` / request budget
5. verify collection status

For a partial canary checkpoint, the normal cycle must resume from page 2 using the
same receipt generation. Page 1 must not be fetched again.

For a same-KST-day COMPLETE QWGJK checkpoint, the next automatic cycle must reuse the
verified checkpoint and perform zero QWGJK source fetches.

## 9. Duplicate-worker protection

The release has two independent guards:

- one worker thread per Python process
- one PostgreSQL advisory operational-cycle lease across overlapping processes

During a rolling Cafe24 deployment, an old and new process may coexist briefly, but
only one process may execute source I/O. The other process retries the lease after
the configured short retry interval, default 15 seconds.

## 10. One-time 4.0 scope migration

On the first 4.0 backend initialization, the one-time scope reset removes obsolete:

- shopping-wide legacy RAW/checkpoints/classifications
- goods/service/opening/award/contract lifecycle RAW/checkpoints
- removed award/lifecycle/contract projections

It preserves:

- budget datasets
- admin/auth
- source credentials
- general settings

The migration has durable markers. After the destructive reset is committed, a later
snapshot-cleanup retry must not delete newly collected 4.0 shopping target data again.

## 11. Final release acceptance

Before changing PR #164 from Draft / HOLD:

- VERSION.txt = 4.0.0
- PR mergeable = true
- branch behind main = 0
- `vnext-runtime-smoke` = SUCCESS
- `g2b-vnext` = SUCCESS
- real PostgreSQL contract = PASS
- least-privilege PostgreSQL contract = PASS
- operational process lease = PASS
- canary page 1 -> automatic page 2 resume = PASS
- same-day COMPLETE source-skip = PASS
- unified HTTP PostgreSQL readiness = PASS
- full pytest / compile = PASS
- actual live source canary has not been confused with CI synthetic-source validation

## 12. Current release rule

Do not enable bulk historical, service/bid/award/contract collection, or education
live transport as part of the 4.0 deployment.
