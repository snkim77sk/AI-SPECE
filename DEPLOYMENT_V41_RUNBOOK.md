# G2B vNext 4.1 Cafe24 Release Runbook

This runbook is the deployment handoff for **SINSUNG G2B VNEXT 4.1.62**.

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

- shopping delivery requests from 2026-09-01 forward; collector, source guard, and storage-scope boundary all use the same date
- only lighting/pole target detail rows are stored from shopping
- next-fiscal-year AIDFA appropriation is checked first; current-fiscal-year AIDFA baseline is also collected before QWGJK and rechecked once per newer KST date so the 2026 budget scope includes annual appropriation context
- completed future AIDFA scopes are rechecked on a newer date so early 0-row results do not become permanent
- QWGJK current state remains on the newest operational snapshot while historical snapshots begin at 2026-01-01 and then follow a rolling source-safe 365-day window through D-1 using remaining LOFIN quota
- historical QWGJK snapshots use a separate `history:year:date` checkpoint namespace, persist normalized observation/revision history only, and do not move current state backwards; once a history scope is COMPLETE, bulky page/item receipts are compacted immediately while the small COMPLETE marker remains
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
G2B_APP_SCHEMA=g2b_app
G2B_BUDGET_SCHEMA=g2b_budget
G2B_V41_FRESH_START=1

# The two workload schemas must be distinct and neither may be g2b_meta.
```

Source credential contract:
- `G2B_SERVICE_KEY`: shopping delivery requests
- `LOFIN_API_KEY`: current QWGJK + rolling QWGJK history + current-year AIDFA baseline + next-year AIDFA budget reads
- `EDUINFO_API_KEY`: stored credential only; live transport remains HOLD

Configure exactly one effective PostgreSQL connection source. When Cafe24 already
provides `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME` (or equivalent `DB_*`
system variables), keep those variables unchanged and do **not** duplicate the
credentials into `G2B_DATABASE_URL`. If no platform variables are available,
configure `G2B_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DBNAME`.
The resolver also supports `PG*`, `POSTGRES_URL`, `POSTGRESQL_URL`, and
`DATABASE_URL`. If multiple sources exist, `G2B_DATABASE_URL` has precedence.

Production does not accept `G2B_BUDGET_DATABASE_URL`; that name is reserved only
for isolated test-mode SQLite fixtures. Never delete or rewrite Cafe24 system
`DB_*` variables just to satisfy the G2B configuration.

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
has not yet been removed. Startup caches the verified marker result, so `/health`, `/ready`, and the settings page expose the safe marker state without issuing another PostgreSQL query.

After the normalized/no-RAW fresh-start succeeds and `/ready` reports `fresh_start_marker_ok=true`, **remove `G2B_V41_FRESH_START`**. While the flag is still present, diagnostics report `fresh_start_flag_enabled=true`; after removal and restart it must be `false`.

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
G2B_CURRENT_APPROPRIATION_SYNC_MAX_PAGES=16
G2B_BUDGET_HISTORY_RESERVE_REQUESTS=20
G2B_VNEXT_API_DAILY_LIMIT=900
LOFIN_VNEXT_API_DAILY_LIMIT=100
```

Control/shopping and budget code share the same SQLAlchemy PostgreSQL pool. This
avoids the 4.0 pattern of independent application and budget connection pools.

`/api/collection-status` exposes the same separation as `source_quota.shopping` and `source_quota.budget`; reading these counters performs no source-network request. `/api/status` also exposes `shopping_storage_ready` and keeps shopping operational readiness independent from budget-schema readiness.

Shopping catch-up can scan up to 62 incomplete dates per cycle. Each date is still
bounded to at most 40 pages and 64 source-request permits including retries, so one
high-volume date cannot consume the full daily allowance by itself. Reaching the
local 900-request ceiling returns shopping `WAITING_QUOTA` instead of a generic
failure and preserves the page checkpoint for the next KST day.

The two API request budgets are independent: `G2B_VNEXT_API_DAILY_LIMIT` applies only
to 나라장터 shopping delivery requests and is code-capped at 900, while
`LOFIN_VNEXT_API_DAILY_LIMIT` applies only to 지방재정365 QWGJK/AIDFA and is
code-capped at 100. Environment values may lower these safety ceilings but cannot
raise them. Each actual retry reserves another request before network I/O; once a
ceiling is reached, the next attempt is blocked before network I/O. Reaching one
limit must not block the other source.

## 6. First boot acceptance

Keep:

```text
G2B_AUTO_SYNC=0
```

Verify in order:

1. `/__ai_space_health` -> HTTP 200; process-liveness only, no storage/budget readiness access
2. `/live` -> HTTP 200
3. `/health` -> HTTP 200
4. `/ready` -> HTTP 200 and `fresh_start_marker_ok=true`, `fresh_start_marker_value=NORMALIZED_NO_RAW_V1`
5. source-free preflight
6. key-aware preflight; verify `shopping_infrastructure_ready`, `budget_infrastructure_ready`, `shopping_collection_ready`, and `budget_collection_ready` independently. `/api/status` must likewise report `shopping_operational_ready` and `budget_operational_ready` independently. `CONFIGURE_POSTGRES_CONNECTION` means use one supported source: `G2B_DATABASE_URL`, Cafe24 `DB_*`, `PG*`, or a supported platform PostgreSQL URL.
7. bounded source canary on disposable storage: shopping + QWGJK + current-year AIDFA + next-year AIDFA. G2B and LOFIN canary results are independent, so one source key/error does not suppress the other source diagnostic.
8. one-page QWGJK deployment canary on production PostgreSQL
9. checkpoint/resume verification
10. keep `G2B_AUTO_SYNC=0`; enable automatic collection only after separate owner approval

A database outage must not collapse `/__ai_space_health`, `/live`, or `/health` to a platform 502. `/__ai_space_health` is intentionally storage-free.
A full readiness failure must make `/ready` return 503. A budget-only readiness failure may also return `/ready=503`, but it must not turn the common backend or shopping collector into a budget-dependent failure.

## 7. Readiness contract

Production UNIFIED readiness requires:

```text
backend_ok=true
db_persistent=true
storage_backend=POSTGRESQL_UNIFIED
budget_postgres_configured=true
budget_postgres_ready=true
fresh_start_status=SKIPPED or COMPLETE
fresh_start_marker_ok=true
fresh_start_marker_value=NORMALIZED_NO_RAW_V1
operational_ready=true
```

When the PostgreSQL connection is missing, the required boot variable is
`G2B_DATABASE_URL`. `/health` and `/ready` also expose `database_source`; it reports only the selected environment-variable family (`G2B_DATABASE_URL`, `DB_*`, `PG*`, etc.) and never the host, user, password, or full URL.

## 8. Collection safety

The existing safety model remains:

- one worker thread per Python process
- one PostgreSQL advisory operational-cycle lease across processes
- shopping target classification is computed from the transient source row during normalized persistence; production shopping does not scan RAW for classification
- operational catch-up defers redundant post-classification calls across dates; the production batch-end classifier returns `NORMALIZED_AT_INGEST` without DB scanning
- shopping/foundation/page-receipt schemas are prepared once at catch-up start and reused across date scopes; date loops do not rerun schema DDL checks
- shopping RUNNING/FAILED/INCOMPLETE scopes keep full page/item receipts for resume
- shopping COMPLETE scopes persist a compact checkpoint marker containing generation, counters, contract/fingerprint, completion reason, and a digest of page response hashes; full page/item receipts are then deleted in the same terminal transaction
- existing pre-marker COMPLETE scopes are verified once and promoted to the compact marker on the next operational scan
- shopping page persistence is transactional: normalized target rows + page receipt + next-page checkpoint commit together
- quota/network interruption resumes the same shopping scope from its persisted next page; verified COMPLETE dates do not refetch
- a successful continuation from a verified partial checkpoint reports `resumed=true`; invalid evidence is replayed as a new generation and is not labeled resumed
- transactional normalized record + page receipt + next-page checkpoint
- bounded next-year AIDFA canary is read-only and production-DB isolated
- production QWGJK canary page 1 -> resume page 2
- same-day COMPLETE checkpoint -> no duplicate QWGJK fetch
- unresolved QWGJK snapshots survive the short receipt window and are resumed before opening a newer snapshot
- RUNNING/FAILED/INCOMPLETE active-generation receipts are protected up to the long retention boundary
- a newer COMPLETE snapshot in the same fiscal year marks older unresolved nationwide checkpoints SUPERSEDED

The advisory automatic-cycle lease name is `g2b_v41_operational_cycle`. Manual source execution is split: shopping uses `g2b_v41_manual_shopping`, and LOFIN budget uses `g2b_v41_manual_budget`. The two manual buttons also use separate credentials, quota counters, and checkpoints.

## 9. Data policy

The 4.1 reset intentionally discards the pre-4.1 G2B dataset.

- Source JSON is transient and is not persisted in production.
- Budget history is rolling 365 days, with QWGJK historical backfill starting at 2026-01-01. QWGJK history retention is based on the official snapshot/source date, not the later backfill ingestion timestamp.
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
budget cycle. Future AIDFA receives priority. When Jan-1 history remains, current QWGJK leaves a bounded reserve (at most 25% of the remaining allowance, capped by `G2B_BUDGET_HISTORY_RESERVE_REQUESTS`) so current and historical state can both make forward progress.

The runtime hard-caps `LOFIN_VNEXT_API_DAILY_LIMIT` at 100 and
`G2B_VNEXT_API_DAILY_LIMIT` at 900. Raising either environment variable above its
cap has no effect; change the audited code contract only after the official source
allowance is confirmed.

Do not add Kafka, Redis, object storage, or separate worker infrastructure until
measured load requires it.


Manual shopping and budget run-state diagnostics are isolated; one source finishing or failing must not overwrite the other source's active state.


Automatic all-source collection uses an exclusive global lease. Manual shopping and budget collection use a shared global lease plus their own exclusive source lease, so the two manual APIs may run together while neither overlaps the automatic all-source cycle.
