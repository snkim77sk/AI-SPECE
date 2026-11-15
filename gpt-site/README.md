# Windows server bridge — v5.2.0 (2026-11-15)

The existing private GPT Site is the final query destination. Cafe24 G2B is retired from the active workflow by the owner. Preserve the existing menus, filters, exports and analysis; Windows remains the collector and RAW authority. Provider credentials and quotas stay on Windows.

`POST /api/server-sync` accepts at most999 verified public-source records per page (12MiB decoded), normalizes them with the existing delivery/budget logic, and atomically commits independent import receipts, overlap keys, current rows, budget history/observations and query revisions. No provider API or cloud collection state is touched. Identical pages are idempotent; conflicts roll back. Older imports cannot overwrite newer rows. Incomplete imports never retire missing records. Existing rows and old collection holds remain. Receipt totals describe pages transferred, not nationwide completeness.

Windows uses the existing owner-private Sites service access header for this exact Site. If the audience is ever changed, add independent write authorization before exposing the updater. No service credential is part of this repository. The UI polls stored revisions every60seconds and reloads after a change when the user is not editing a field. Code version and data revisions are displayed separately.

Validation:682 Site tests (the XLSX check requires Python openpyxl), TypeScript and production build;406 real source pages /380534 records normalized and committed in a separate local SQLite database, with no source provider calls. Existing comparison and export functions remain covered. See the Windows repository release report for actual hosted upload/readback and scheduling evidence.

Earlier notes below describe historical cloud-collection work and are retained as history; the Windows bridge above is the current operating path.

## v18 replacement development (2026-11-07)

Added full-filter QWGJK exact summaries, cross-project stored unique-state history,
exact AIDFA-to-QWGJK evidence links, Incheon and historical-region read filters,
word-wise search, vendor aliases/business numbers, visible delivery identifiers,
page-only exact totals/calculated unit-price labels, and matching field propagation.
See `MIGRATION_PARITY.md` for remaining code and operational gaps. This is not a
complete daily observation ledger, complete replacement acceptance, or live-source
verification. Provider keys and unattended schedules remain absent.

# G2B private workspace — v15

Code-only port of the existing shopping-delivery scope. No production data import, deletion, server cutover or sharing change. The user approved live operation and a 950/day KST allocation from their stated 1,000/day allowance; the subsequent action-time approval allows connecting the two existing keys, but that transfer was blocked before execution by the old environment usage limit. No keys have been retrieved or configured here.

## Scope and behavior
- Nationwide ShoppingMallPrdctInfoService/getDlvrReqDtlInfoList only. Service/bid/award APIs are not called.
- Exact 19 lighting + 3 pole detail codes, matching existing shopping_scope_v4.py. Smart control 3911980401 is stored separately from ordinary views from 2026-10-01.
- Source-date window: max(2026-01-01, KST today minus 27 calendar months) through KST D-1, with month-end clamping. Out-of-window data is hidden; automatic destructive retention is not enabled.
- Most recent three days first, then ascending backlog. Date/page checkpoints persist in D1.
- Fetched, retained and excluded counts are separate. All source identities are recorded to reject duplicate/overlapping pages, even when a row is excluded.
- Detail amount uses prdctAmt or exact decimal unit price × quantity, rounded once. Request-total dlvrReqAmt is a separate field and never summed per item.
- Mode-isolated normalized records, retained source payloads, bounded search and current-page CSV export.
- Records include source change orders, current selection and inactive history. Full authoritative population coverage and explicit cancellation interpretation remain unverified.

## Performance and limits
- Candidate/default maximum page: 999, matching the existing Python collector contract. Current provider acceptance requires a real-API smoke test; no such call has occurred yet.
- Bulk SQL, not one query per row. A normal-sized page uses a bounded batch of transactional statements, including source revision metadata and end-of-day reconciliation. Larger JSON bindings split below 1.4 MB to respect D1's documented 2 MB value/row limit.
- 8 MiB source response cap; this is not an 8 MiB memory bound. Oversized individual storage records fail closed.
- No Cafe24 memory admission, GC-wait, or recovery scheduler is ported.
- Exact source bytes are hashed before parsing; no whole-response reserialization for the hash. Parsed rows and normalized arrays are released after preparing their next representation.
- Bounded status response; full aggregate summary is not recomputed after every collected page.
- Cloudflare Worker platform memory is 128 MB and is not configurable through the current Sites tool schema. Peak hosted memory has not been measured.

## Real API and unattended operation
- Credentials are absent as of implementation. No real API calls or existing data imports have occurred.
- G2B_SERVICE_KEY must be a Sites secret, never source/client content/chat text.
- Real calls require G2B_LIVE_ENABLED=approved and G2B_DAILY_BUDGET as an explicitly allocated integer from 1 to 950. This budget must account for the existing server's use of the same provider allocation. The local counter does not know other callers' usage.
- No unattended automation is configured. The UI's continuous mode runs only while its page remains open. No secret is stored or activated without the required approval.
- Legacy v1 trial endpoints cannot create a live run after this upgrade.

## Validation
node --test tests/*.test.mjs
npx tsc --noEmit

D1 page records, source identities, receipt, day checkpoint, and session cursor are committed in one transaction with lease fencing. Pause and cancel invalidate expired lease tokens. Lost client responses recover by reading persisted state. Duplicate pages roll back all inserts.

Hosted service access is restricted to the verified owner-private Site. Browser writes check Origin and JSON Content-Type. Do not broaden the Site audience without adding server-side membership/owner controls appropriate to that audience.

## v3 migration progress
- User-approved G2B cap is 950 attempts per Korean day. The existing collector is reported stopped; its already consumed daily calls are not visible here. Sites cannot enforce a provider-wide cap across other callers.
- Current queries select latest active changes and suppress older target items when a newer non-target revision is observed. Full nonempty-day reconciliation preserves old rows as inactive history. Empty complete scans never deactivate a whole day.
- Minimal source identity metadata is retained for all observed rows; payload JSON remains only for target details. Key bindings are chunked below D1 value limits too.
- Business-number-aware vendor ranking, collected-institution totals, query date/region/history filters, 50/100/250-row views, and separate smart fixture/control views are implemented. These are based on collected coverage, not a guarantee of complete national coverage. During collection totals are provisional.
- Smart control 3911980401 is stored only for dates from 2026-10-01, excluded from ordinary lighting/pole views. Six smart fixture codes share ordinary lighting storage. One source read serves both views rather than consuming a duplicate API quota.
- A protected one-page automatic action is prepared: POST /api/collection with action=automatic. dryRun=true performs no writes or source fetch. Actual unattended use additionally requires G2B_AUTOCOLLECT_ENABLED=enabled and a separately configured supported schedule. Neither is enabled. Pause/cancel and non-quota errors stop unattended progress; a quota error can resume after the next KST date.
- Amount analysis rejects individual values outside signed 64-bit range before SQL CAST; an aggregate overflow fails closed. Row values and CSV remain decimal strings. This is not arbitrary-precision SQL aggregation.
- Remaining full replacement work at v3 (see subsequent progress below): LOFIN QWGJK/AIDFA source collectors, budget history and filters, budget/procurement matching and evidence, sales leads, budget XLSX/full export, source coverage certification, unattended schedule connection, and real-key operational checks. LOFIN requires a separate user-entered secret and retains its distinct existing 500/day cap. Existing education-budget transport was on HOLD and is not represented as working.

## Bounded performance cache
Vendor/institution results and current-count cards use a 60-second D1 cache keyed by normalized query and a per-mode data revision. Every collection-page transaction also increments that revision; cached results cannot survive a committed page change. Revision changes during computation prevent a stale write. The cache holds at most 64 payloads per mode, each at most 512 KiB. It is durable storage, not an unbounded in-memory cache, and cached failures are never saved.

## Budget source migration (v6)
QWGJK and AIDFA now have independent durable page sessions, source-wide LOFIN daily quota counters, strict envelope/paging validation, normalized current state, immutable unique semantic revisions, source payload evidence, nonempty-complete reconciliation, history-only backfill, search and current-page CSV. QWGJK `exe_ymd` alone is excluded from semantic change hashes. Older snapshots cannot replace newer current rows. Explicit zero, missing and invalid money remain distinct; AIDFA execution/remaining are null rather than fabricated zero/unspent claims. The UI clearly labels synthetic mode. No live LOFIN requests have been verified or permitted by its runtime gate. `LOFIN_API_KEY`, `LOFIN_LIVE_ENABLED=approved`, and a separately confirmed `LOFIN_DAILY_BUDGET <= 500` are needed. No values are configured by this version.

v4 concurrency retest found 5/30 failed search responses. v5 diagnostics identified an overload/rate-limit class on uncached concurrent queries (8/30 errors); repeated cached queries passed 30/30 but cold initial network-inclusive latency remained high. v6 replaces the full-range ranking window with indexed newer-version exclusion, allowing outer filters to narrow candidates before latest-state checks. This does not claim to raise platform limits or eliminate every overload. Live G2B/LOFIN dual collection and unattended scheduling are still unverified.

A v6 production query regression was traced to SQLite choosing the date-range index for the correlated newer-candidate lookup, producing quadratic work. The next patch pins that lookup to idx_delivery_current and asserts its EXPLAIN plan. This is a query repair, not a platform-memory increase.

## Unattended writer contract
`/collection-workflow.json` holds credential-free instructions and `/api/automation` supports one atomic page per POST with source `g2b`, `QWGJK`, or `AIDFA`. Dry runs never activate sources. G2B and LOFIN need their own configured secrets, live approval, daily allocations and automatic-runner flags. An eligible future task must obtain supported Site access afresh, keep it in memory, stop a source on error or performed=false, read back uncertain outcomes, and never override pause/cancel. Only network timeouts can retry after a15-minute cooldown; malformed source/schema/storage failures remain stopped. QWGJK refreshes the current daily snapshot; AIDFA is a weekly collection-time snapshot and cannot claim historical dates. AIDFA sessions crossing a KST day stop the old page and the automatic writer preserves the old session, then restarts a fresh collection-day snapshot. User pauses/cancellations are never overridden.

A proposed initial15-minute schedule may process at most10 G2B pages and5 combined LOFIN pages or90 seconds per run, whichever comes first. This is not an installed schedule and actual API latency/allocation remain unverified. Existing budget history backfill planning, matching, sales candidates and XLSX are still incomplete.

## v9 budget review and accuracy
- Current budget rows have recent-date, agency/project, exact budget and exact remaining-balance ordering with keyset pagination. Signed integer text ordering avoids silently overflowing SQLite integer casts. Zero, null and negative balances are distinct.
- QWGJK lighting/pole sales-review candidates require a positive provided remaining balance and valid amounts. This does not establish planned procurement. Execution status and exact region/agency/department filters are supported; AIDFA cannot use detail-execution or sales-candidate filters.
- List/history queries remove the raw source object in SQL before transfer and parsing. Full source evidence stays in D1. Current first100 CSV remains explicitly bounded, not full export.
- Source numeric amounts outside JavaScript safe integer precision fail or flag invalid instead of silently rounding; malformed/oversized budget identities stop the page. An explicit invalid budget is not treated as missing and replaced with appropriation.
- An unknown-total LOFIN run can terminate on a true no-data final page without treating that final zero as a contradictory total for preceding pages.
- The request-grouping and matching engine is present as a tested pure module only. It is NOT connected to the database, query UI or persisted global evidence. Complete request groups, indexed candidates and global winner consistency are required before enabling it.
- Browser QA verified synthetic candidate sorting, next100 traversal, execution filtering and AIDFA mode restrictions. All data and performance tests remain synthetic with zero public-provider calls.
- Remaining: budget historical automatic planning, connected global budget/procurement matching and evidence, bounded XLSX/full export, confirmed source coverage, and real credentials/quota/scheduled operation. No old service/data cutover has occurred.

## v10 daily history and year coverage
- QWGJK history fills the oldest missing nationwide calendar day from max(2026-01-01, KST today minus365 days) through D-1. Completed current snapshots and completed history sessions both count. The history ledger reads all bounded-window completions, not the UI latest8. Current rows remain unchanged.
- Current/history lanes coexist with independent durable page cursors and a shared atomic LOFIN daily quota. The proposed runner must share its5-page LOFIN cycle cap and reserve history opportunities without starving current refresh. This is a scheduling contract, not an installed scheduler.
- Durable per-mode source/history controls preserve pause/cancel even if another session is created or a new date arrives. Migration backfills stopped controls from the latest preexisting lane session. Explicit operator resume is required.
- AIDFA supports current and next fiscal years, daily refresh and resuming unfinished pages across collection days. Multi-day observations are marked as collection periods, never reproducible historical snapshots. Multi-day completion does not retire missing current rows. This changes v8's conservative restart policy, which could otherwise starve large datasets.
- Automation lanes now include QWGJK_HISTORY and AIDFA_NEXT; all retain the missing-key, approval, quota and automatic-runner gates. No real key or schedule is configured.
- Browser QA: history1000 rows paused, blocked further collection, resumed at page2, completed2407 on the same day, next date advanced by one day, and current2026-11-06 rows stayed unchanged. Next-year AIDFA2027 synthetic2407 rows completed with null execution/balance fields.
- Remaining full replacement work: persisted request-safe budget/procurement matching and evidence, bounded full/XLSX export, real-source coverage/quotas and unattended scheduled runtime verification.

## v11 persisted matching
- Analysis jobs freeze source revisions and the retention window. Per-mode budget epochs increment in the same transaction as current budget writes; history-only backfills leave the current epoch unchanged. Source changes stop further matching writes and mark older results stale.
- Budget candidates are indexed by supported organization aliases. Current/latest target details stream in request/source-key order, materializing at most500 rows/1MiB per D1 response. A request can span many pages; only a verified next request or full-source EOF closes it. Its serialized accumulator is capped at1MiB and excessive inputs fail explicitly instead of being truncated.
- At most250 pairs and8 completed requests are processed per matching step. Candidate pages retain the best winner until every supported candidate is examined. One request is assigned to one best collected budget, with exact BigInt totals, missing-amount counts, integrity exclusions and persisted evidence. Missing monetary rows do not receive the amount-plausibility score.
- Atomic leases, source-epoch guards and page/cursor writes prevent duplicate outcomes after conflicting writers or lost responses. Paused/cancelled jobs never advance implicitly.
- Request-date filters select requests, not partial detail subsets: all retained current target details for those requests are grouped. Matching only covers collected data; incomplete national source coverage is never labeled complete.
- SQL/actual-service regression tests cover1203 details across page/date boundaries,600+ candidate budgets across comparison pages, pause/cancel, source changes and transaction rollback. Pure streaming tests additionally cover20000 details with bounded accumulator state. These are synthetic tests, not provider or hosted-memory measurements.
- The operating handoff is in OPERATIONS.md. Full/XLSX export, administrative date lineage, real-provider checks and verified unattended scheduling remain outstanding.

## v12 bounded matching throughput candidate
Budget indexing is raised from500 to1000 rows and pair scoring from250 to1000 pairs per step while preserving the1MiB D1 response limit, eight-request processing bound, complete-request aggregation, atomic checkpoints and source revision fences. GROUP remains500 rows because its streaming accumulator uses that validated append contract. This is batching optimization, not a larger Worker memory allocation. Real hosted comparison against v11 is required before claiming a speedup.


## v12 matching batching measurement
Same hosted synthetic workload (10,000 budget candidates, 4 requests, 976 comparisons, 2 winners; pause/resume included) measured16.321s in v11 and12.391s in v12, a24.1% reduction in this single comparison. First authenticated read latency was excluded (12.209s then9.090s). All calls succeeded. This is not a general guarantee, equivalent Cafe24 benchmark, live API proof, or peak-memory measurement.

## v13 bounded search-result exports
- The original active app exported only current detailed-budget results, at most10,000 rows. Sites now adds matching16-column XLSX and CSV exports using the current filters/order, independent of the visible page cursor. AIDFA and revision-history rows are explicitly rejected by this endpoint.
-500-row keyset reads are additionally bounded to1MiB in SQL before materialization. Rows are encoded and discarded; no full raw source payload or second complete object list is retained. Prepared output is bounded to32MiB before a download response is sent. That bound is a fail-closed safeguard within the fixed128MiB platform isolate, not a measured peak-memory claim.
- Every page checks a source revision. A change aborts the export before any partial download is returned. No provider API calls, source changes or persistent export credentials are involved.
- XLSX uses a dependency-free stored ZIP/OOXML writer with inline strings, exact identifiers, highlighted headers, frozen row1, filters, and a separate metadata sheet. Explicit zero remains0; missing money is blank. Values exceeding Excel's15-digit precision remain exact text. This deliberately fixes the original exporter’s missing-as-zero and float-rounding behavior.
- A10,001st-row lookahead makes truncation explicit in headers/UI/metadata. Exports identify actual versus synthetic mode. CSV retains BOM/escaping/formula-injection guards. No totals or formulas are invented.
- Remaining: verified administrative boundary/date lineage and source completeness, real credentials/quota validation, unattended scheduling and real-world acceptance. Existing production data/service remains untouched.


## v14 export query optimization
The first hosted v13 10,000-row XLSX attempt returned503 DATABASE_OVERLOADED after6.8s Worker wall time/208ms Worker CPU. No provider call or source mutation occurred. The local162-test suite and browser2,407-row export success did not prove large hosted export reliability.

Dedicated current-budget ordering indexes now cover recent, organization/project, exact budget and exact remaining-balance order. EXPLAIN regression checks ensure the source selection uses these indexes before bounded row materialization, avoiding repeated whole-scope JSON monetary sorting. Small window sorts remain bounded to501 input rows. The Drizzle generator split commas within expression indexes; the new unapplied migration SQL was repaired against its correct schema snapshot before first application. No applied migration was rewritten. Hosted large-export retest remains required before claiming success.


## v14 hosted validation outcome
The corrected hosted run exported10,000 XLSX rows (11,057,722 bytes) in5.155s and10,000 filtered CSV rows in7.514s. Independent ZIP/XML/openpyxl checks confirmed16 columns, numeric ordering,10,000 unique project codes, metadata/truncation, frozen header, filter, and no formula cells. Three simultaneous10,000-row exports all returned200 in10.364–13.013s. No OOM was observed; peak memory remains unavailable. Initial authenticated access took15.472s (Worker252ms), so cold-path latency is not resolved. All tests were synthetic, provider calls0.

## v15 conservative administrative relationships
- Confirmed official effective dates: Incheon2026-07-01; Hwaseong four general wards2026-02-01; Jeonnam-Gwangju top-level merger2026-07-01. Public source URLs are in lib/admin-history.mjs and the matching UI.
- Candidate-family keys broaden retrieval only. Final scoring requires matching known regions, exact former/successor organizations, correctly directed dates (former before, successor on/after), and a shared exact locality token for Incheon. Missing/invalid dates, different numbered localities, generic/place substring matches and other regions fail closed.
- Hwaseong city itself remains in existence. Its original label/key is never emptied or retired. Explicit parent/ward scope relation is only a capped79-point review candidate and never proof of organizational succession or actual procurement authority. No automatic current-agency aggregation rewrite was added.
- Jeonnam-Gwangju applies only to exact top-level government names; lower-level cities/districts do not inherit the merger alias. Source agency/region fields remain unchanged in all result evidence.
- Existing matching jobs receive legacy engine version; old results become stale and old pending engines cannot continue under new rules. A fresh plan is needed. No source records or previous evidence are deleted.
- Other administrative transitions and nationwide completeness remain unverified. Date-aware current-government regrouping across all agency reports is intentionally not inferred from project location. Actual credentials/quota/unattended runtime remain blocked prerequisites.

- Independent review exposed two false-high cases before v15 publication: a missing region could fall through general aliases, and a transition locality was counted twice as organization and project identity. Known regions are now required on both sides (and invalid-region rows are not candidate-indexed). Transition locality/ward tokens are removed from independent project-identity scoring. With only generic lighting text the pair is excluded; shared LED signal can yield at most a review candidate; separate facility/project identity is needed for HIGH. Regression tests cover both cases.

- Further review covered multiple administrative-locality tokens and explicit organization/region conflicts. All verified administrative identity terms are excluded from independent project scoring, mixed successor areas cap confidence, and conflicting explicit organization-region labels fail closed. The four review reproductions are blocked; administrative regressions11/11 and the full176 tests passed. Browser QA confirms old engine results are marked stale and official-source/limitations text is visible.

## v16 budget navigation and stored project detail
- Added the original region → institution → department selection flow. Stored current rows supply nationwide choices; the original Seoul/Gyeonggi base list supplements empty places and is explicitly not a complete national registry. Source code identity groups name variants without merging different codes/regions. Uncoded names only collapse whitespace.
- Institution and department choices are capped at500 with explicit more-result flags and independent search, including department-code search. Parent/source/year changes clear child selectors. The same selector predicates are used by current lists and CSV/XLSX exports; old exact-name filters remain available.
- Added read-only current project detail with region, institution, department/code fallback, project/code, field/section/account, source layer/date, exact money and execution rate, plus at most100 distinct saved-state revisions. Raw source is removed in SQL and detail transfer is capped at1MiB. This is not complete daily existence history.
- Independent review found blank fallback, alternate department-code names, numeric code and case-insensitive search bugs. These were fixed; department code extraction now matches supported normalization aliases and is shared with export.
-182 tests and TypeScript pass. Browser interaction checks passed on existing local synthetic records: region and institution search, department filtering, detail and saved history, then resetting selectors when switching source. Real API calls0, no key access, no new schedule, no old server/data changes. Hosted read-only verification follows publication.
- Remaining key-free parity priority: bounded repeated/overlapping-page replay recovery and unified operational status. Real provider coverage, approved per-service allocations and an installed/verified unattended schedule remain incomplete.

## v16 hosted read-only outcome
The deployed synthetic dataset passed organization cap/search, department selection, exact detail/source isolation, and matching list/export predicates. Single warm requests were188–1,851ms; first access6.863s. Three parallel reads all succeeded in4.224s. No provider call or source mutation occurred. Peak memory remains unmeasured.

## v17 bounded overlap recovery and operational status
- Confirmed QWGJK cross-page source-key overlap is now distinguished from storage failure. A bounded JSON join checks prior seen keys before commit; the existing unique key remains the transaction's final defense. Duplicate keys within a page, total/schema failures, AIDFA and unrelated storage failures are not replayed as drift.
- One new session generation may restart at page1. Durable parent/root links, a unique parent constraint, CAS/lease/control checks, successor creation and old-session retirement share a transaction. Prior rows, revisions, seen keys and receipts remain. History-only recovery never changes current rows. Partially collected current data stays provisional; only a complete nonempty generation can retire absent rows.
- The second overlap is permanently marked exhausted for that generation. Resume cannot clear the independent marker, and subsequent manual or automatic attempts make no source call. A fresh explicitly created plan is a separate operator revalidation; automatic planning cannot silently create a fresh root for the exhausted unfinished scope.
- Manual recovery and an already-enabled current/history runner use the same function. An automatic replay tick only creates metadata; the next page uses the normal daily quota. Existing key/approval/allocation/runner gates, sticky pause/cancel and history priority rules remain. No scheduler has been installed, no source key exists and no real provider call was made.
- Added a read-only unified operational page with five lanes, independent checkpoints/errors, actual KST attempt counters, shared LOFIN allocation, history coverage/planner reasons and recovery consumption. It reuses the actual history planner, including prior-year pending history. Stored payload maximum is explicitly not peak memory, and external quota usage remains unknown.
- Offline fetch-mocked tests cover overlap rollback, success/exhaustion, simultaneous restart, injected transaction failure, pause/lease races, quota persistence, history/current isolation and enabled-runner gates. Browser checks used a clearly synthetic local error-state fixture: pause disabled replay, explicit resume allowed one fresh page1/zero-row session, and the monitor showed its paused1/1 state. The browser fixture alone does not prove provider drift; actual detection is covered by service tests.
