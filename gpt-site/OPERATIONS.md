# G2B private Sites operational handoff

## Verified deployment and access
The application is owner-private. No other users or external visitors have been granted access. Existing G2B services and production databases have not been deleted, restarted or cut over. Existing records are not imported; the new application recollects permitted sources.

## Before the first real collection
1. Use the official Sites settings for this existing private project. Add `G2B_SERVICE_KEY` and `LOFIN_API_KEY` as secrets, never source code, chat text, URLs in logs or frontend settings. The user approved connecting the two old keys, but the old environment stopped before retrieval or storage. No secret has been moved.
2. G2B's configured local allocation is950 attempts per KST day from the user's stated1,000 allowance. The existing collector is reported stopped. Confirm its already-consumed calls and all other callers in the actual provider account/service scope; the local counter cannot see them. Only after that review set the non-secret G2B_QUOTA_SCOPE_REVIEWED value to allocation-confirmed. Adding a key alone must remain blocked.
3. LOFIN's existing500/day application maximum is separate and its actual allocation remains unconfirmed. Confirm the provider allowance before setting `LOFIN_DAILY_BUDGET` to an integer1..500 and `LOFIN_LIVE_ENABLED=approved`. Do not infer that the G2B1,000 allowance covers LOFIN or NO1. After reviewing the actual LOFIN allocation and all callers, set LOFIN_QUOTA_SCOPE_REVIEWED to allocation-confirmed. Neither flag is a cross-site shared reservation store or proof of current provider remaining quota.
4. Apply the environment changes through the official supported flow and deploy the same verified source. Read only key-presence metadata and readiness, not secret values. `GET /api/automation` reports current gates. A true hasKey flag is not proof of provider acceptance.
5. With remaining provider quota verified, run one live page per intended source and read back its receipt, source date/year, detail identities, checkpoint and actual provider count. G2B999/page and LOFIN1000/page still require real-provider acceptance tests. Failed attempts consume the local permit too.
6. Compare representative live fields to the old application rules before enabling unattended operation. Synthetic tests do not establish actual source completeness, schema compatibility or daily volume.

## Supported unattended writer, not an installed schedule
`POST /api/automation` takes `source` and `dryRun`.
- `g2b`: shopping-delivery current/backlog.
- `QWGJK`: current nationwide detail-execution snapshot.
- `AIDFA`: current-year structural appropriation.
- `AIDFA_NEXT`: next-year structural appropriation.
- `QWGJK_HISTORY`: oldest missing nationwide daily history within365 days, without changing current rows.

The credential-free execution contract is `/collection-workflow.json`. A future authorized schedule obtains fresh supported service access for this same private Site. Keep its bearer only in memory and the same Site's `OAI-Sites-Authorization` header; do not save it in source, prompts or logs.

The proposed initial cadence is every15 minutes in Asia/Seoul, at most90 seconds,10 G2B pages and5 **combined** LOFIN pages per invocation. Daily server quotas remain the final atomic guard. Current and next-year AIDFA/current QWGJK have priority, while history receives reserved opportunities so it is not starved. No schedule is currently installed and actual scheduled-runtime execution remains unverified.

Only enable `G2B_AUTOCOLLECT_ENABLED=enabled` and/or `LOFIN_AUTOCOLLECT_ENABLED=enabled` after the matching source/key/quota smoke checks and the scheduler's official access path are verified. Pause/cancel controls are durable and must never be overridden automatically. Stop on non-transient errors. Network timeouts can retry only after15 minutes; read persisted checkpoints before retrying uncertain writes. Quota errors can recover on the next KST day.

## Source and analysis behavior
- G2B: nationwide exact lighting/pole code scope, services excluded,2026-01-01/rolling27-month window through D-1, smart controls separated, current latest revisions and retained evidence.
- QWGJK: current detail execution plus daily historical gaps up to365 days. Current and history share LOFIN quota but have separate checkpoints.
- AIDFA: current and next fiscal year, daily observations; multi-day continuation is a collection period, not a reproducible historical snapshot. Multi-day completion does not deactivate unseen current rows.
- Matching: indexes collected current budgets, groups entire eligible requests across detail pages, then evaluates all supported agency-alias candidates before one winner per request. Source mutation stops the job; old results are marked stale. This is candidate evidence, not proof of budget execution, planned procurement or nationwide source coverage. Verified Incheon2026-07-01, Hwaseong2026-02-01 and Jeonnam-Gwangju2026-07-01 relationships provide guarded candidate evidence only. Original agency/region identities remain unchanged; general nationwide date-lineage regrouping is not inferred. Old engine results are stale and require a fresh plan.

## Acceptance before old-service cutover
Verify real daily refresh and interruption recovery with the page closed, both providers running within allocation, secret/log safety, actual source coverage, query latency including cold starts, source field normalization, current/history reconciliation, matching evidence and required exports. No cutover or old-service removal is authorized merely by this checklist. Peak Worker memory is not exposed by current tooling;128MiB is the fixed platform ceiling, not a measured working set or an adjustable setting.

## Page drift and unified status
`/collection-monitor` and read-only `/api/collection-status?mode=live` show the same durable source/history decisions without requesting provider data. Runtime schedule installation is still false.

Confirmed QWGJK cross-page overlap can restart once from page1, either through the explicit budget-screen button or the already-approved/enabled runner. `POST /api/budget` action `replay-overlap` takes the old session ID; `dryRun:true` is read-only. A replay tick changes metadata only and does not reset daily usage. Failed provider responses already consumed their quota. Subsequent pages use normal permits. Old receipts/rows are preserved, current data remains provisional until normal completion, and history-only generations do not alter current data. The second overlap stops further calls; inspect source ordering/coverage before an explicitly new plan. Pause/cancel outranks replay. AIDFA, within-page duplicates and arbitrary storage/schema errors never use this recovery.

## v19 observation and overview reads

New QWGJK page commits write dated references in the same atomic batch as revision/receipt/checkpoint updates. No prior unique state is backfilled. `/api/budget/observations` uses explicit filters, source-revision cursors and strictly earlier observed-day baselines. Same-date multiple payloads do not establish event order. The physical ledger is not proof of complete population or daily presence. `/api/dashboard` reports stored/current/history counts, key presence and readiness without source calls. It does not measure physical bytes or peak memory. Institution/future pure modules are code-only building blocks, not an installed workflow.

## v20 observed institution builds

`/institution-patterns` and `/api/institution-patterns` build derived summaries from one complete, current matching job. Source collection and the original matching assignments stay unchanged. Create includes a request_id for response-loss readback; repeat the same request_id only for the same source/mode. The three phases project projects, project selected requests, and stream events. All inserts and cursors share fenced transactions. Paused/cancelled runs do not automatically resume. Completed snapshots remain readable with stale metadata after source changes. Full population counts/rates remain unknown. This is not future-opportunity global ranking, cross-job request reconciliation, complete administrative lineage or scheduled analysis.

## v21 future evidence read

`/future-evidence` is a read-only view of stored target-year budgets and pre-target evidence. First complete a current institution analysis created with alias indexing enabled. Old v20 institution runs remain readable but do not claim that index exists. A new derived run from the same matching job adds it without fetching provider data. Changing source datasets invalidates the analysis and requires a new current matching snapshot.

AIDFA/QWGJK remain separate source layers. The default rolling history selects2 latest stored years from the same immutable matching job; missing calendar years are not invented. Each target may link to only one direct, same-region/category organization.25row pages are source-ordered, not globally score-ranked. No complete-population ratio or procurement probability is asserted. Full future ranking, lineage pooling, cross-job deduplication, real provider acceptance and unattended operation are still open.

## v22 non-destructive storage inspection

The storage-inspection screen reads one named area per click. It aggregates selected text UTF-8 lengths and counts inside D1; it does not return raw data. Selected-payload bytes are not physical storage usage or execution memory. Older-date rows remain preserved and are not automatically eligible for deletion. No cleanup operation is exposed. Review observations, active/current rows and saved-analysis references before proposing any retention change. This diagnostic can help identify duplicated payload-heavy areas without raising the fixed128MiB platform ceiling.

## v23 allocation review is a required runtime gate

Manual plan creation, existing page steps and all unattended lanes now require the provider-specific reviewed-allocation flag before local quota reservation or source fetch. Missing review yields G2B_QUOTA_SCOPE_NOT_REVIEWED or LOFIN_QUOTA_SCOPE_NOT_REVIEWED. Revoking review stops subsequent pages without resetting usage. Synthetic flows remain independent. No environment flags were enabled by this change, no credential was set, and no installed schedule is implied. Re-review allocation whenever service/account/caller scope materially changes; do not turn the flags on merely because a test needs to run.

## v25 and v26 current derived workflows

Current institution runs use conservative configured lineage with a fixed KST as-of date and retain original names/regions. Unsupported split clues and ambiguous identities remain separate. Old completed analysis snapshots stay readable as stale; rebuild from a current matching snapshot rather than mixing versions.

`/future-ranking` is a durable bounded review workflow, not global ranking. The original default200/max500 latest-source selection is followed by exact evidence/amount/name ordering. `/api/future-ranking` offers workspace/results reads and create/step/pause/resume/cancel. Creation uses an idempotency UUID bound to all conditions; save it before transmission and read back uncertain outcomes. One step commits at most25results with its checkpoint. Byte-short result pages carry compact run-scoped UUID cursors. Dataset/source changes stop unfinished work, while completed historical results display stale status.

All ranking options belong to the saved run; editing the new-run form does not change that run. Closing the browser stops its next-step loop, and reopening never silently resumes it. An unattended collector/schedule remains a separate acceptance requirement. Current source privacy depends on the unchanged owner-private Sites gate, not on POST origin checking alone.

## v31 key-registration readiness verification
The server code accepts the exact case-sensitive secret names `G2B_SERVICE_KEY` and `LOFIN_API_KEY`. The native Sites environment API supports `is_secret=true`, and its current contract explicitly requires deploying a saved version after environment updates. A metadata-only read found neither key, environment revision1 unchanged. Do not send values in chat, source, logs or this app's browser forms.

The existing UI links to https://chatgpt.com/sites. In this verification the route redirected to the ChatGPT sign-in page; the authenticated user's exact settings menu and direct secret-entry form could not be inspected. The tool surface contains environment update/get operations but no dedicated user secret-entry widget. Therefore do not promise a verified one-click secure entry screen or invent its menu names. Registration-ready code is distinct from verified user access to the secret form and from safe provider activation. Adding keys alone leaves shared-allocation gates closed. Original G2B950 and the separate unconfirmed LOFIN allowance still require provider/caller scope review, and actual collection/schedules remain disabled or unverified.

## v5.2.18 server-independent contract and budget review

- The existing owner-private Site can read **already committed** delivery/budget records while the Windows uplink is unreachable. `/contract-review` uses only the existing `GET /api/deliveries`, `GET /api/budget`, `GET /api/server-sync` and `GET /api/automation`. Read endpoints do not fetch from the provider.
- Operators may select a local CSV/TSV/JSON file to review new contracts and budgets immediately (2 MiB/2,500 rows max). This preview is in browser memory only, not a persistent import or authoritative data feed. Files are not uploaded to the Site. File data is cleared by reload, page exit or explicit reset. XLSX/PDF documents must first be converted to a supported structured format; avoid exporting embedded formulas or private credentials.
- User-visible date, amount units and source freshness must be checked. A non-empty local review, a receipt sum, or an old Site row must not be called newly collected or nationally complete. Do not claim manual preview rows have been persisted or joined to current budget matches.
- Existing Sites direct collection still requires the already defined secret, quota allocation and manual approval gates before live requests. The new page does not change schedule status or issue a provider call, even on load.
- Owner-private access remains unchanged. Actual Site deployment requires an authorized official session; GitHub `main` updates do not automatically publish an updated Site. Before reporting production readiness, verify authenticated route, data lookup, local parsing and signed source freshness.
- The sync API protocol constant stays on `windows-verified-pages-v1` and `SYNC_VERSION=5.2.16` for wire compatibility. Display version 5.2.18 describes source candidate only, not the running Site. Windows `D:\G2B`, NO1 and the original operating DB remain untouched.

## v5.2.19 public-site candidate, disabled by default

See `PUBLIC_RELEASE_CHECKLIST.md` for required social OAuth setup, existing-D1 additive migration, exact redirect origins, verified first-administrator bootstrap, service-token uploader cutover, access-denial smoke tests, rate limiting and privacy checks. The current GPT Site stays owner-private, and environment flag `G2B_PUBLIC_AUTH_ENABLED` must remain unset until reviewed and approved. Daum uses Kakao integration, not a separate OAuth client. No data deletion, new production database, actual public deployment or Windows/NO1 changes were made by the candidate source.
