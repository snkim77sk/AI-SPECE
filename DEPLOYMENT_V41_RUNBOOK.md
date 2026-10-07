# G2B vNext 4.1 Cafe24 Release Runbook

This runbook is the deployment handoff for **SINSUNG G2B VNEXT 4.1.179**.

4.1 is a storage-contract reset, not an in-place 4.0 data migration. The owner
approved discarding the existing G2B 4.0 dataset and rebuilding it from official
sources.

## 1. 4.1 storage contract

Production has one PostgreSQL database.

- `g2b_app`
  - administrator/session state
  - application settings and source credentials
  - normalized 2026-01-01+ lighting/pole business records
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

The long-running PostgreSQL application role does not need full database-owner
privileges, but it is not DML-only. The 4.1 runtime performs idempotent app-schema
table/index installation checks, so the role must retain CONNECT, workload-schema
USAGE, app-schema CREATE, and the required table DML privileges. Fresh-start
drop/create operations still require the separate bootstrap/owner capability.

## 2. Scope remains narrow

Allowed operational source domains:

- shopping delivery requests from 2026-01-01 forward; collector, source guard, and storage-scope boundary all use the same date
- only lighting/pole target detail rows are stored from shopping
- next-fiscal-year AIDFA appropriation is checked first; current-fiscal-year AIDFA baseline is also collected before QWGJK and rechecked once per newer KST date so the 2026 budget scope includes annual appropriation context
- completed future AIDFA scopes are rechecked on a newer date so early 0-row results do not become permanent
- QWGJK current state remains on the newest operational snapshot while historical snapshots begin at 2026-01-01 and then follow a rolling source-safe 365-day window through D-1 using remaining LOFIN quota; the operational current snapshot also uses D-1, and an older zero-row COMPLETE current checkpoint is replayed once on a newer KST day instead of being trusted forever
- historical QWGJK snapshots use a separate `history:year:date` checkpoint namespace, persist normalized observation/revision history only, and do not move current state backwards; once a history scope is COMPLETE, bulky page/item receipts are compacted immediately while the small COMPLETE marker remains
- QWGJK budget collection is normalized on receipt; source JSON is not persisted
- budget read views use the stored top-level region code/name to provide nationwide or 17-region filtering without source traffic; education-budget rows use the same canonical region mapping when live transport is later approved
- the budget UI separates collected current-state rows from sales-target/prebid rows, so AIDFA structural budget records remain visible even when they are intentionally not promoted to direct sales candidates
- collected current-state rows are read directly from canonical normalized `g2b_budget` state/projects rather than depending on the app-side projection; target/prebid analysis remains projection/classification based

Still delegated / blocked:

- goods bid notices -> NO1
- service notices -> NO1
- opening results -> NO1
- award/opening source collection and bid prediction -> NO1
- compact budget-execution award evidence -> G2B source-free import/storage only; source guard remains closed
- contracts -> NO1
- bulk historical -> HOLD
- APPROVED_HISTORICAL -> HOLD
- education live transport -> HOLD

4.1 changes storage, not this source boundary. 4.1.136 adds a validated one-way `g2b-execution-evidence-import-v1` document contract. It rejects NO1 prediction/raw fields, caps one document at 5,000 rows, stores only compact matched evidence plus source/digest provenance, and still does not allowlist award/contract source APIs. 4.1.135 adds only a source-free compact evidence layer for already-obtained official service/work award rows; it does not allowlist ScsbidInfoService and does not connect to the NO1 database.

Budget screen contract in 4.1.137: QWGJK detail/execution rows are the primary operational view. Category filtering is performed in PostgreSQL against exact-current budget classifications before pagination; project/organization search and execution-state filters are storage-side. Historical budget-to-shopping matching remains stored/reference-only and is no longer the primary screen workflow.

Budget UI contract in 4.1.139: Incheon is the default region and `INCHEON_ALL` is the default institution scope. Current Incheon districts and major city agencies are storage-side filters applied before pagination; QWGJK detail/execution remains the primary table.

Budget classification repair in 4.1.140: after backend/schema readiness and before recurring source collection, the unified runtime source-free classifies already-stored normalized budget rows whose PostgreSQL `budget_classifications` row is missing/stale. Existing budget facts, revisions and checkpoints are preserved.

## 3. Current 4.1.x redeploy

Use the Cafe24 PostgreSQL service that belongs to the G2B project. Current
redeployments preserve the existing normalized rows and collection checkpoints. Real PostgreSQL CI verifies that both budget and shopping can cross a fresh Python-process boundary and resume the persisted checkpoint without replaying the already committed page.

Required:

```text
G2B_TEST_MODE=0
G2B_RUNTIME_ROLE=UNIFIED
# Memory-safe default: web + DB/schema first, source work only by explicit opt-in.
G2B_AUTO_SYNC=0
G2B_AUTO_SYNC_DISABLE=0
G2B_POST_BOOT_MAINTENANCE_ENABLE=0
G2B_MATCH_ROLLOVER_AUTO_ENABLE=0
G2B_MEMORY_SOFT_LIMIT_MB=160
G2B_APP_SCHEMA=g2b_app
G2B_BUDGET_SCHEMA=g2b_budget
G2B_V41_FRESH_START=0
# Optional fallback when the platform does not provide GITHUB_SHA.
# GITHUB_SHA is authoritative whenever both are present:
G2B_BUILD_COMMIT=<deployed Git commit SHA>

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

## 4. Historical one-time fresh start

Do **not** run this procedure on a normal 4.1.x redeploy. It exists only for an
explicitly approved legacy 4.0 -> 4.1 destructive reset.

On that one-time migration boot:

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

After the normalized/no-RAW fresh-start succeeds and `/ready` reports `fresh_start_marker_ok=true`, set `G2B_V41_FRESH_START=0` (or remove the variable). While the flag is still enabled, diagnostics report `fresh_start_flag_enabled=true`; normal operation must report it as `false`.

## 5. Shared PostgreSQL pool

Recommended defaults:

```text
G2B_DB_POOL_SIZE=1
G2B_DB_MAX_OVERFLOW=1
G2B_DB_POOL_TIMEOUT_SECONDS=5
G2B_DB_POOL_RECYCLE_SECONDS=900
G2B_DB_CONNECT_TIMEOUT_SECONDS=3
G2B_DB_LOCK_TIMEOUT_MS=5000
G2B_DB_STATEMENT_TIMEOUT_MS=120000
G2B_FUTURE_BUDGET_SYNC_MAX_PAGES=24
G2B_CURRENT_APPROPRIATION_SYNC_MAX_PAGES=16
G2B_BUDGET_HISTORY_RESERVE_REQUESTS=20
G2B_SHOPPING_RECHECK_DAYS=7
G2B_SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN=2
G2B_SHOPPING_RETENTION_MONTHS=27
G2B_SHOPPING_RETENTION_BATCH_SIZE=2000
G2B_VNEXT_API_DAILY_LIMIT=900
LOFIN_VNEXT_API_DAILY_LIMIT=500
```

Control/shopping and budget code share the same SQLAlchemy PostgreSQL pool. This
avoids the 4.0 pattern of independent application and budget connection pools.

`/api/collection-status` exposes the same separation as `source_quota.shopping` and `source_quota.budget`; reading these counters performs no source-network request. `/api/status` also exposes `shopping_storage_ready` and keeps shopping operational readiness independent from budget-schema readiness.

Shopping catch-up can scan up to 62 incomplete dates per cycle. Each date is still
bounded to at most 40 pages and 64 source-request permits including retries, so one
high-volume date cannot consume the full daily allowance by itself. Reaching the
local 900-request ceiling returns shopping `WAITING_QUOTA` instead of a generic
failure and preserves the page checkpoint for the next KST day.

4.1.141.3 keeps recurring source work OFF after backend/schema readiness. Set `G2B_AUTO_SYNC=1` only after web/DB readiness is verified. `G2B_AUTO_SYNC_DISABLE=1` remains the emergency kill-switch. New heavy work is refused when process RSS reaches the configured soft limit or the cgroup effective-pressure wait threshold. On a 256 MiB cgroup the adaptive thresholds are about 208 MiB wait / 224 MiB block.

The automatic all-source cycle calls sources in this order: shopping backlog, next-year AIDFA, current-year AIDFA, QWGJK current, then QWGJK history. A shopping-family failure does not convert the independent budget source state to FAILED, and budget failures likewise do not rewrite the shopping source state.

The two API request budgets are independent: `G2B_VNEXT_API_DAILY_LIMIT` applies only
to 나라장터 shopping delivery requests and is code-capped at 900, while
`LOFIN_VNEXT_API_DAILY_LIMIT` applies only to 지방재정365 QWGJK/AIDFA and is
code-capped at 500. Environment values may lower these safety ceilings but cannot
raise them. Each actual retry reserves another request before network I/O; once a
ceiling is reached, the next attempt is blocked before network I/O. Reaching one
limit must not block the other source. When quota is the only remaining blocker,
the automatic worker sleeps until just after the next KST date boundary and resumes
the preserved checkpoint; if the independent source is still PARTIAL, the normal
collection interval remains in effect so that source can continue.

The collection monitor gives explicit runtime wait states precedence over an old
RUNNING checkpoint. `WAITING_QUOTA`, `WAITING_KEYS`, and storage waits are shown
as normal wait states rather than `STALE`, and they are excluded from the
error/stopped count.

## 6. First boot acceptance

For a normal 4.1.141.3 UNIFIED redeploy, recurring collection remains OFF after backend/schema readiness. Keep `G2B_AUTO_SYNC=0` while verifying the web process and PostgreSQL. Set `G2B_AUTO_SYNC=1` only after explicit approval. `G2B_AUTO_SYNC_DISABLE=1` remains an emergency kill-switch and always wins.

Verify in order:

1. `/__ai_space_health` -> HTTP 200; process-liveness only, no storage/budget readiness access; verify both `version` and `build_commit`
2. `/live` -> HTTP 200
3. `/health` -> HTTP 200
4. `/ready` -> HTTP 200 and `fresh_start_marker_ok=true`, `fresh_start_marker_value=NORMALIZED_NO_RAW_V1`
5. source-free preflight
6. key-aware preflight; verify `shopping_infrastructure_ready`, `budget_infrastructure_ready`, `shopping_collection_ready`, and `budget_collection_ready` independently. `/api/status` must likewise report `shopping_operational_ready` and `budget_operational_ready` independently. `CONFIGURE_POSTGRES_CONNECTION` means use one supported source: `G2B_DATABASE_URL`, Cafe24 `DB_*`, `PG*`, or a supported platform PostgreSQL URL.
7. bounded source canary on disposable storage: shopping + QWGJK + current-year AIDFA + next-year AIDFA. G2B and LOFIN canary results are independent, so one source key/error does not suppress the other source diagnostic. A live invocation is fail-closed: missing keys, failed/inconclusive shopping, QWGJK schema failure, or unacceptable AIDFA transport/schema evidence returns a nonzero workflow exit.
8. one-page QWGJK deployment canary on production PostgreSQL
9. checkpoint/resume verification
10. confirm `G2B_AUTO_SYNC_DISABLE` is absent/0; UNIFIED automatic collection should report enabled and resume stored checkpoints

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
- only after baseline is COMPLETE through D-1, production rechecks the most recent 7 source dates at most once per KST day; dates freshly collected in that run count as already checked
- recent recheck is bounded to 7 dates and the existing 64-request-per-date guard; backlog collection always takes priority over recheck
- after recent recheck, production rotates through older COMPLETE source dates at most 2 dates per KST day using a persistent next-date cursor; a run that performed baseline catch-up does not run long-tail recheck
- shopping retention is an exact 27 calendar months (2 years 3 months). The calendar-month retention floor bounds normalized rows, shopping checkpoints/residual receipts, baseline catch-up, recent recheck and long-tail recheck together, so purged dates cannot be recollected by later cycles
- orphan shopping page/item receipts are purged directly by one-day scope date even when the matching checkpoint no longer exists; the retired local compatibility collector uses the same 27-month source floor and the same effective 62-day run cap
- LOCAL_COLLECTOR still uses isolated SQLite, but shopping collection/readback and result snapshots use normalized `shopping_records`; snapshot-only cycles run the same 27-month shopping retention before exporting results, so legacy RAW cannot override new 4.1 shopping rows
- LOCAL_COLLECTOR environment overrides are cycle-scoped: runtime role, test-mode/SQLite selectors, database URLs and service-key state are restored after every successful or failed cycle
- normalized shopping persistence requires a canonical `YYYY-MM-DD` source date on/after 2026-01-01; retention defensively removes legacy blank, malformed, or pre-bootstrap source-date rows so they cannot evade the 27-month rolling window
- expired shopping rows are deleted in bounded transactions (default 2,000 rows, hard bounds 100..10,000); expired receipt/checkpoint cleanup is split by source-day scope to avoid one large retention transaction
- recent 7-day + long-tail 2-day theoretical request ceiling is 576 requests/day at the per-date 64-request guard, leaving headroom below the 900-request local G2B cap
- a larger positive shopping `totalCount` may extend the same generation; receipt verification accepts only monotonic positive growth. This exception is shopping-only; budget/other collectors keep strict total-drift rejection
- a smaller total, premature empty page, total underrun, or overlapping page marks the scope INCOMPLETE; the unstable page is not normalized, and the next cycle replays that date from page 1 in a fresh generation; if `REPEATED_OR_OVERLAPPING_PAGE` repeats, the replay page size adapts from 999 to 500 and then 250, and a RUNNING fallback checkpoint keeps that page size on the next cycle
- shopping page size remains internally capped at 999; do not increase it without explicit source documentation or live validation
- before shopping COMPLETE receipts are compacted, the full one-day generation reconciles normalized rows transactionally: observed target identities stay/reactivate, observed non-target identities become inactive with `OUTSIDE_TARGET_SCOPE`, and previously active identities missing from the complete source day become inactive with `MISSING_FROM_COMPLETE_SOURCE`
- inactive shopping rows are retained as change-order/history evidence; production shopping/vendor read models default to active rows only
- shopping monitor/dashboard counts distinguish current active rows, total preserved history, and inactive history; legacy `records` count remains total history for compatibility
- a zero-row COMPLETE shopping scan is fail-safe and does not deactivate an entire previously populated source day
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
- Non-budget business records start at 2026-01-01.
- Shopping delivery history is backfilled from 2026-01-01 forward; older non-budget material is not backfilled.
- Goods/service bid and award/contract domains remain delegated to NO1.

## 10. Long-term scaling rule

Keep one PostgreSQL database and the current CONTROL/BUDGET/READ responsibilities
until measured workload requires otherwise. Because 4.1 does not retain source JSON,
there is no planned RAW data-lake tier.

The operational collector reads the remaining LOFIN daily allowance before each
budget cycle. Future AIDFA receives priority. When Jan-1 history remains, current QWGJK leaves a bounded reserve (at most 25% of the remaining allowance, capped by `G2B_BUDGET_HISTORY_RESERVE_REQUESTS`) so current and historical state can both make forward progress.

The runtime hard-caps `LOFIN_VNEXT_API_DAILY_LIMIT` at 500 and
`G2B_VNEXT_API_DAILY_LIMIT` at 900. Raising either environment variable above its
cap has no effect; change the audited code contract only after the official source
allowance is confirmed.

Do not add Kafka, Redis, object storage, or separate worker infrastructure until
measured load requires it.


Manual shopping and budget run-state diagnostics are isolated; one source finishing or failing must not overwrite the other source's active state.


Automatic all-source collection uses an exclusive global lease. Manual shopping and budget collection use a shared global lease plus their own exclusive source lease, so the two manual APIs may run together while neither overlaps the automatic all-source cycle.


## Deployment identity and destructive-reset safety

Normal operation keeps `G2B_V41_FRESH_START=0` and `G2B_DESTRUCTIVE_RESET_CONFIRM=0`.
If prior G2B storage exists, schema reset requires both values to be explicitly set to 1.
A stale `G2B_V41_FRESH_START=1` alone cannot drop PostgreSQL schemas.

`/live` and `/health` expose the actual platform/checkout commit, source fingerprint,
process instance ID/start time/uptime, and a local deployment verdict. Optional
`G2B_EXPECTED_BUILD_COMMIT` and `G2B_EXPECTED_SOURCE_FINGERPRINT` can fail the
diagnostic verdict to STALE when a specific deployment target is expected.


## Launcher recovery in 4.1.168

Normal production remains `python run.py -> uvicorn -> main:app`. If Uvicorn
or the initial ASGI import exits abnormally, run.py falls back to a stdlib-only
HTTP server on the same PORT. The fallback never opens PostgreSQL or calls source
APIs. `/live` and `/health` remain HTTP 200 for platform diagnosis while
`/ready` remains HTTP 503 until the real application can boot.


## 70-second liveness gate

4.1.169 adds a Python 3.12 + PostgreSQL CI gate that keeps the normal UNIFIED
runtime alive for 70 seconds, explicitly crossing the historical ~45-second
failure boundary. It verifies /live continuously, /health at 45 and 70 seconds,
zero OOM-kill events, process RSS and peak RSS below the 160 MiB soft limit, and
no unexpected isolated heavy worker while automatic collection/maintenance are OFF.


## Result-sync memory boundary

4.1.170 caps RESULT_SERVER snapshot ingestion before allocation can threaten a
256 MiB container. Compressed/chunked request bodies are streamed and hard-capped
at 4 MiB. Gzip-expanded JSON is capped at 12 MiB. Snapshot import no longer makes
a second recursive copy of the full parsed payload. Memory pressure returns HTTP
503 instead of attempting the import. Oversized local snapshots fail before upload.


## Isolated result-sync worker

4.1.171 keeps authenticated RESULT_SERVER uploads out of the long-lived web
process. The request body is streamed to a bounded temporary file and a disposable
worker performs gzip expansion, JSON parsing and snapshot import. The worker uses
the common heavy-work lock, parent watchdog, oom_score_adj=900 and the isolated
worker RSS soft limit. If cgroup memory.oom.group=1 or memory pressure is unsafe,
the sync request fails closed with HTTP 503 while the web process remains alive.


## Serving SQLite path safety

4.1.172 never derives a filesystem path from the PostgreSQL logical locator.
For RESULT_SERVER compatibility mode, G2B_SERVING_DB_PATH remains authoritative;
without it production uses /app/user_data/g2b-serving.sqlite3. Test/local SQLite
continues to place g2b-serving.sqlite3 beside the local DB fixture.


## PostgreSQL result-server maintenance

4.1.173 treats legacy result-server SQLite compaction as not applicable on the
production PostgreSQL backend. The maintenance action returns SKIPPED_POSTGRESQL
without resolving a SQLite path, querying sqlite_master, dropping tables, or
running VACUUM. The old compaction path remains test/local-SQLite only.
