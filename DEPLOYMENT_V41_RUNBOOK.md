# G2B vNext 4.1 Cafe24 Release Runbook

**SINSUNG G2B VNEXT 4.1.296 (DRAFT / NO DEPLOY):** On the disposable 256MiB real cgroup CI environment, keep web alive with Cafe24-like 113.3MiB synthetic effective baseline while classifying >=2,477 synthetic budget current rows from the previous 999/1000-row and 424/477 deep-cursor fixtures. Run unchanged `classification_vnext.classify_dataset` over three budget datasets with max 250 rows per batch /16 batches, confirm exact-current PostgreSQL classification SHA join, zero redundant reclassification, prior 477 receipt integrity, real Worker VmHWM, cgroup sampled pressure +64MiB reserve, HTTP liveness and no OOM. This is not real Cafe24 data or source API access and cannot yet authorize relaxing the protected 112MiB forecast +64MiB prelaunch child admission (minimum96MiB). No main merge or Cafe24 deploy without explicit user approval.

**SINSUNG G2B VNEXT 4.1.295 (DRAFT / NO DEPLOY):** A disposable GitHub-only real 256MiB web/PG cgroup and approximately 113.3MiB effective prelaunch ballast now exercise synthetic QWGJK resume at **page 424/477**. Preseed 423 prior valid **one-row-per-page** normalized observations and receipt hashes, confirm verified_checkpoint, fail with MemoryPressureError *after fetching page424 but before its transaction*, retain INCOMPLETE checkpoint/receipt generation, restart in a new OS process at page424, complete through page477, and prove completed replay makes zero source fetches. This is synthetic deep-cursor ABI testing, not 423,000 production rows; V4.1.293 independently exercised one 1,000-row page. External APIs/credentials and real Cafe24 PostgreSQL are never used; the existing 112MiB forecast +64MiB reserve and 96MiB minimum are NOT reduced. No main merge/deploy without user approval.

**SINSUNG G2B VNEXT 4.1.294 — CAFE24 113.3MiB PRELAUNCH EFFECTIVE BASELINE (DRAFT / NO DEPLOY):** A dedicated disposable CI Python ballast retains anonymous memory to make the *measured* cgroup effective occupancy ~113.3MiB in a real 256MiB web container BEFORE source launch. New unique CI-only shopping 999 and LOFIN budget 1,000 synthetic rows are then checkpointed one Worker at a time. 256MiB memory.max and all web /live·/ready, oom_kill, Linux process peak RSS, sampled cgroup pressure and at least 64MiB remaining effective headroom are checked. No source APIs, real production credentials, or Cafe24 production PG access. The test bypasses the production parent launch preflight by directly executing children; it does not establish that today's 112MiB forecast +64MiB headroom policy permits source launching at 113.3MiB. The admission policy and production main V4.1.288 remain unchanged. HOLD release until explicit authorization and real checkpoint/live-data evidence.

**SINSUNG G2B VNEXT 4.1.293 (DRAFT / NO DEPLOY):** Synthetic 999-row shopping LED and 1,000-row QWGJK budget pages run in the existing, actual 256MiB CI web+isolated-Worker cgroup. Call the production page-transaction/checkpoint code but use generated-only inputs, disabled external transports, disposable PostgreSQL, separate source runs, worker VmHWM and sampled cgroup peaks, HTTP /live and /ready, and OOM Kill 0. This does not prove actual 424/477 resume, external source API payloads or production 112+64MiB child admission. Main V4.1.288, normalized data and formulas remain unchanged. No release without operator approval.

**SINSUNG G2B VNEXT 4.1.292 (DRAFT / NO DEPLOY):** Adds a GitHub-only Docker memory.max=256MiB test that runs the real web plus one disposable shopping/LOFIN child at a time in the SAME 256MiB cgroup with isolated CI PostgreSQL and BLANK source credentials. Assert /live and /ready survive, source state WAITING_KEYS, numerical worker VmHWM and sampled cgroup pressure, and no new OOM Kill. The test launches children directly, so passing does NOT prove the original parent child-admission gate (112MiB forecast +64MiB reserve) permits collection. No real source API pages or 424/477 resume tested. Do not merge/deploy without explicit user approval.

**SINSUNG G2B VNEXT 4.1.291 — SOURCE PAGE RSS POST-FETCH SAFETY (DRAFT / NO DEPLOY):** Immediately after source response and before hashing/writing each shopping or LOFIN page, run the existing cooperative cgroup and RSS checkpoint. If denied, use the existing resumable INCOMPLETE receipt boundary. On graceful Worker exit log process ru_maxrss high-water and *sampled* cgroup effective/current maxima via `G2B_SOURCE_WORKER_MEMORY`. No source API, secrets, table reset, limit change, page size or business formula modification. Existing 112MiB child forecast, minimum 96MiB and 64MiB web reserve must remain enforced. On Cafe24 effective 113.3MiB source starts still HOLD until safely verified. main 4.1.288 must not be merged/deployed without user approval.

**SINSUNG G2B VNEXT 4.1.290 — SOURCE-ONLY WORKER LIGHTENING (DRAFT / NO DEPLOY):** For 256MiB UNIFIED isolated shopping/budget worker, skip FastAPI route/web auth/result snapshot imports and verify the *already installed* PostgreSQL marker and schema with read-only catalog SELECTs. Collector data/quotas/matching stay unchanged, as does 512MiB+ full worker compatibility. The source-free CI profile records shopping/budget import high-water RSS, **not** live page-processing peaks. Web cgroup effective 113.3MiB still fails the existing >=96MiB child +64MiB reserve admission. Do not weaken that limit without evidence. A verified operational collection and non-502 Cafe24 run are outstanding. Keep main V4.1.288 unchanged, do not merge/deploy and never delete/reset PostgreSQL data.

**4.1.289 256MiB STAGGERED SOURCE AUTO-SCHEDULE (DRAFT / NO DEPLOY):** Operating main V4.1.288 is retained. In a production UNIFIED cgroup <=320MiB or unreadable limit, schedule only ONE automatic source in each KST two-hour slot: shopping 00/04/08/12/16/20 and LOFIN budget 02/06/10/14/18/22, hence **4h per source separated by 2h**. An accepted or completed source is not automatically queued twice within the same slot; incomplete checkpoint, memory hold, partial recovery and explicit key/manual wake retain existing retry semantics and memory/PG advisory protections. Existing 512MiB and higher instances continue their old serialized collector protocol. No simultaneous shopping/budget isolation children are permitted by the existing singleton.

**CRITICAL 256MiB BLOCKER:** The previous functional operation is consistent with the later 4.1.232 introduction of conservative child preflight. Current observed web cgroup effective ~113.3MiB; forecast child 112MiB and 64MiB reserve demands total 289.3MiB > 256MiB, so alternating source schedules CANNOT make that child runnable. Do not misreport automatic scheduler ON as successful API collection and do not lower source admission without proof. To restore 256MiB collection, audit a truly lean Worker with measured maximum real-process cgroup spikes and preserved web availability, or separately configure a genuinely larger cgroup after operator choice. PostgreSQL normalized rows, checkpoint identities, source quotas and existing 502 guards unchanged.


**4.1.288 FINAL IDLE RECOVERY SAFETY AUDIT (PR #343, NO DEPLOY):** 4.1.287's web-side 15-minute GC/malloc_trim monitor previously omitted `/collection-monitor` from active requests, and could overlap DB-backed status renders. It now counts every status request as active but does not reset idle clock on a background 30-second refresh. A process RSS/HOLD and CGROUP_WAIT pressure now also prompt a low-frequency safe idle-only attempt. A final new isolated Worker could start after the memory monitor's prior `workers_busy()` check; the web reclaim cycle now owns the same `_ISOLATED_HEAVY_LOCK` used to launch new children for the duration of GC/trim and skips immediately if it cannot acquire that mutex. No SQL, source call, process restart/kill, persistent-file delete, workload formula or operating Worker admission change.

At 256MiB, Worker memory admission still requires **effective prelaunch cgroup usage strictly below 80MiB** for the original 112MiB child and 64MiB reserve. Reclaiming GC/heap may free 0 bytes; NEVER infer that Cafe24 platform free space is safe Worker headroom or override the guard. The prior V4.1.287 shopping COMPLETE-only 4h cadence and budget 2h cadence remain unchanged. Review exact 4.1.288 GitHub CI and actual Cafe24 status (healthy 4.1.285 before merge) before an independently approved controlled deployment. User database/checkpoints must remain intact.


**4.1.287 LOW-MEMORY IDLE RECLAIM + SHOPPING COMPLETE 4H THROTTLE (DRAFT):** Current main V4.1.285 remains untouched. Candidate stacks V4.1.286 current-KST-day filter plus idle-only G2B memory reclaim modeled on SINSUNG. Web runs a tiny 15-minute monitor; after backend ready, if no active heavy workers/supervisors or actual web requests, it may run `gc.collect` + `malloc_trim` (glibc only) without restarting the server, deleting caches/files, database rows, receipts or checkpoints. Usual idle trim 48–72 hours; high cgroup pressure or unchanged 112+64MiB source-child admission failure may trim after 45 idle seconds, at most hourly. Recovery stats are read-only in `/health` and collection status. No guarantee that a >80MiB warmed 256MiB service becomes eligible to launch a 112MiB child; real cgroup evidence remains required.

**SOURCE FREQUENCY:** Baseline/incomplete shopping checkpoints still resume immediately; successful COMPLETE shopping automatically rechecks no more frequently than once every 4 hours, while budget UNIFIED scheduler stays on the original 2-hour tick, manual source buttons and explicit credential/key wake remain available. Existing 900/500 daily API ceiling, latest date D-1, 7-day recent/2-day longtail controls and read-only date selection are unchanged. This does not turn prior historical data into a bulk fetch.

**Operational caution:** The existing Cafe24 Stage 1 panel shows about 101.8MB/268MB; its platform free capacity is not the same as cgroup-effective incremental child headroom. If actual G2B Web GC/trim cannot bring cgroup effective bytes below admission threshold, do not lower 64MiB reserve or child soft limit to force execution. Upgrade to a verified 512MiB tier or separately audit a genuinely smaller collector. Do not merge PR/deploy until test gates, explicit owner approval, main HEAD/tree and existing Pg data/Worker PID safety pass.


**4.1.286 DATE FILTER + WORKER HEADROOM DIAGNOSTIC (Draft / no main merge):** Current LED/pole stored-data default search now uses KST current-year Jan 1 through the current date, not future Dec 31. Current fiscal-year budget change-history date filter likewise defaults to today; explicit user ranges and historical years remain unmodified. This changes SELECT filters and rendered defaults only: no source API schedule/quota change, source calls or database writes.

On observed Cafe24 tier 268MB, platform used memory 101.8MB is not worker-start headroom. Existing source isolation forecast (112MiB child + 64MiB reserve) requires current effective cgroup usage below 80MiB for a 256MiB container. The live cgroup effective value can differ from platform used memory. The new UI exposes the actual guard fields and 512MiB recommendation when current source state is WAITING_MEMORY due to `CGRP_CHILD_HEADROOM_UNSAFE`. **No memory threshold is reduced**; if 256MiB cannot admit the Worker, use 512MiB and verify real collector/cgroup health. No deletion/reset/checkpoint change or actual Cafe24 deployment is performed by this code review.


**4.1.285 MATCH CHILD RESULT RECONCILIATION (DRAFT / NO DEPLOY):** In the old Match worker, internal FAILED / PARTIAL / WAITING_QUOTA / WAITING_MEMORY were published only to the disposable child's memory, then the function returned None; the isolated child incorrectly exited 0 and the web parent marked the Match runtime IDLE, allowing previously saved match snapshots to masquerade as the new run's completion. The child now emits matching 0/78/73/75/1 exit codes; absent/unknown results fail closed; the parent maps quota 73 to WAITING_QUOTA and startup not-ready 70 to WAITING_STORAGE. V4.1.284 cross-container match advisory locks remain required and unchanged. These source-free result corrections do not change persisted match rows, source quotas, schema, business calculations, memory thresholds or database pool settings.

**Operational scope reminder:** Cafe24 live endpoints could not be fetched from the available inspection environment; it is NOT evidence of 502. All old 4.1.238 web/shop/budget/match PIDs must be verified stopped with restart disabled before a separately authorized merge/deploy, and main auto-deploy trigger must be paused or proven inert. Developer-only GitHub success does not verify actual Cafe24 cgroup, source checkpoint resumes or external API availability.


**4.1.284 CROSS-CONTAINER MATCH WORKER LEASE — PRODUCTION HOLD:** Both shopping and LOFIN workers already use shared/exclusive PostgreSQL source leases. Match Worker previously had only a host-local Python/file lock while replacing materialized matching rows for the same run_key. This cannot serialize matching writes across separate Cafe24 containers. In new code, non-test match recompute and optional legacy 2025 backfill hold dedicated `g2b_v41_match_rollover` PostgreSQL advisory lease; contention yields LEASE_HELD (isolated child 77), connection failure yields sanitized WAITING_STORAGE (74), and neither path performs matching writes or source API calls. The non-mutating cold cutover advisory probe checks this fourth lease as well. This addition requires no schema migration and changes no historical rows, source quota, business calculation, 256/512MiB memory, worker 112+64MiB, web 300s grace or pool1+1.

**Legacy limitation:** 4.1.238 Match Worker does NOT acquire this new match lock. An empty PostgreSQL lock probe cannot verify old idle web/Worker PID exit. Before an owner-approved main merge, independently confirm Cafe24 auto-deploy is paused, all legacy web/shopping/budget/match PIDs have exited and cannot respawn. Keep existing PostgreSQL unchanged; no controlled cutover evidence = NO-GO. GitHub Actions do not constitute live Cafe24 evidence.


**4.1.282 FULL-TREE RELEASE IDENTITY + COLD MAIN CUTOVER (DRAFT/HOLD):** Existing main 4.1.238 HEAD `021de332296e094d917371e8736f6b964ebe380c` and candidate integration PR #342 must remain unchanged until the owner explicitly approves. Do NOT assume a main push is inert: first establish whether Cafe24 automatically deploys or restarts when GitHub main changes. If automatic deployment cannot be paused/disabled and independently confirmed, keep **main MERGE HOLD**. After explicit merge approval, block automatic deployment and old service respawns, stop the old 4.1.238 web and every isolated shopping/budget/match child, verify PIDs have exited and PostgreSQL source advisory locks are free. Only then merge the reviewed cumulative main-target PR. Recheck main HEAD and the exact approved candidate **Git tree SHA** before releasing any new web/worker. A newly changed main or candidate branch invalidates the previous approval and requires re-audit.

Run `python -B scripts/g2b_integrated_release_gate.py --reviewed-main-head <40-character actual merged main SHA> --reviewed-release-tree <40-character independently reviewed candidate TREE SHA> --merged-main-confirmed --old-processes-stopped` from a **clean full Git checkout** of approved main; a .git-free source archive is not sufficient for this tree gate. The locally observed HEAD commit, complete tree hash, exact Git repository root and clean tracked/untracked worktree must all pass BEFORE opening production PostgreSQL. Refuse if any Git metadata is absent, if the Git tree differs, if auto-deploy might have started a new service, or if previous process/lock evidence is missing. The two operator flags remain human attestations, NOT automated proof. `--ci-simulation` using disposable PostgreSQL remains development evidence only, never a production cutover.

Only after the non-mutating gate may an authorized operator explicitly start the new Cafe24 release, verify actual deployed build/source fingerprint, `/live` `/health` `/ready` and menus, 256/512MiB actual cgroup/OOM/Worker headroom, 1+1 PG pool and both G2B/LOFIN source resume checkpoints. Any failure remains HOLD and must NOT be repaired by resetting PostgreSQL, receipts, classifications, app settings, or budgets. Original source API ceilings and business calculations are unchanged.


**4.1.281 CUMULATIVE RELEASE HANDOFF — DO NOT DEPLOY:** 2026-10-11 GitHub 확인상 운영 main 4.1.238 HEAD `021de332296e094d917371e8736f6b964ebe380c` 유지. 누적 #301..#341 Draft PR 41개는 첫 PR main부터 매 PR 이전 HEAD/다음 base SHA가 일치하며, V4.1.280 HEAD `82494b14ea7de15e115a42d0ed7ebd5bfb46f004`는 main보다 64커밋 앞서고 0커밋 뒤처졌고 변경 파일 72개다. V4.1.281 릴리스 후보의 최종 tree는 새로운 main 대상 단일 누적 Draft PR에서 검토한다. 승인 없이 main을 건드리거나 Cafe24에 배포하지 않는다. 기존 stacked Draft PR을 각각 원래 개발 base로 병합해서는 main 전체 반영이 증명되지 않는다. 통합 승인 후에도 실제 main HEAD와 tree가 검토 후보와 일치하는지 검증하며, main 변경 시 전체 재감사한다.

GitHub CI `cafe24` 이름이 포함된 workflow는 **로컬 launcher/uv/HTTP 모의 검사만 실행하고 원격 Cafe24 배포하지 않는다.** CI 4개 SUCCESS/1,621 passed/232파일 정적 0오류/70초 peak RSS 91.65MiB·OOM 0/512MiB 보호 238 passed는 개발 검증일 뿐이다. 실제 256/512MiB 컨테이너 메모리·실제 메뉴 503·실제 쇼핑/예산 수집·중복 Worker 없음은 미검증이다.

운영 전환 단계는 사용자 승인 → main 통합 HEAD/tree 재확인 → **기존 웹/격리 Worker 전부 종료를 원격에서 확인** → 기존 PG advisory lease 해제 → read-only marker/체크포인트 검사 → 실제 새 버전 기동·빌드 SHA·HTTP/메모리/실수집·체크포인트 재개 검증이다. 모든 단계에 분명한 증거가 없으면 HOLD. 구버전이 살아 있거나 잠금이 불확실하면 새 Worker를 시작하지 않는다. `scripts/g2b_deployment_preflight.py`는 DDL 가능한 `init_db()`를 호출하므로 오래된 프로세스와 마커 확인 전에 실행하지 않는다. 기존 PG 데이터·체크포인트·영수증·분류이력 절대 삭제/초기화 금지. 원천 API 한도·사업 수식·256/512MiB/112+64MiB/웹300초/Pool1+1 불변.


**4.1.280 CUMULATIVE DRAFT STACK / NON-MUTATING RELEASE CUTOVER:** GitHub API verification found all 40 PRs #301..#340 are still Draft/open/unmerged, with each PR's base referencing the previous PR's head branch. Main stays 4.1.238 at `021de332296e094d917371e8736f6b964ebe380c`; 4.1.279 development had 62 commits and 70 files ahead. Full GitHub CI does not make Draft PRs deployable. CRITICAL: `scripts/g2b_deployment_preflight.py` invokes `db.init_db()` / budget storage readiness and can perform schema/index CREATE, so it is **not** a read-only check to run before old process isolation. FIRST (after a separately authorized and reviewed main merge, never while Draft): stop ALL 4.1.238 web and shopping/budget/match child PIDs on every Cafe24 instance; operator must see that they have exited. Keep `G2B_TEST_MODE=0`, `G2B_RUNTIME_ROLE=UNIFIED`, `G2B_AUTO_SYNC_DISABLE=0`, `G2B_V41_FRESH_START=0`, `G2B_DESTRUCTIVE_RESET_CONFIRM=0`, same PostgreSQL credentials. From the **reviewed merged release checkout**, run `python -B scripts/g2b_integrated_release_gate.py --reviewed-main-head <40-character merged HEAD> --reviewed-release-tree <40-character independently approved TREE SHA> --merged-main-confirmed --old-processes-stopped`. The new checker opens no PG connection on missing approval/version/HEAD/config, and otherwise runs cold advisory-lock probe → read-only PostgreSQL marker/checkpoint ABI validation → final cold advisory-lock probe using only the already-audited guards. No DDL/DML, no business-row changes, no G2B/LOFIN source calls and no deployment action. A `cutover_evidence_ready=True` report is only bounded evidence: BOTH operator flags are *attestations*, not independently verified PID/PR approval. Always retain separate local PID evidence and the post-deploy Cafe24 /live /health /ready /menus, true 256/512MiB cgroup worker admission and source-checkpoint canary requirements. GitHub Actions only runs a clearly labeled `--ci-simulation` against an ephemeral PostgreSQL database; its "ready" result does not imply any real Cafe24 release approval. No main merge/production deployment yet: HOLD.


**4.1.279 UNREAPED SOURCE SLOT / USER-VISIBLE STATUS:** `Popen.poll()` may report an exit code before the child supervisor publishes a final source state. Previously `running=False` and `reap_pending=False` for a live supervisor could lead to a false operational COMPLETE read and an enabled manual collection button while the same source's Popen remained reserved. Mark `slot_reserved=True` whenever a Popen object exists and `reap_pending=True` for a polled exit, missing reaper or unreadable poll. Without mutating the stored checkpoint/source run state, the runtime-status response distinguishes REAP_PENDING (terminal publishing), REAPER_RECOVERY (missing supervisor), WORKER_STATUS_UNKNOWN (poll failure) and active RUNNING. Keep both source buttons disabled while a corresponding child remains reserved; monitor source labels and stage warnings display the reason. Existing source admission/worker locks continue to prevent actual duplicate child starts; automatic scheduler waits for finalization. No schema/SQL/API/checkpoint/credentials modifications, memory policy or source quota changes. Synthetic fake-Popen, UI-label and queue regressions added. CI remains development evidence; production Cafe24 cutover must stay HOLD.


**4.1.278 REAP-BACKOFF CGROUP MEMORY MONITOR:** The v4.1.277 child-supervisor wait backoff could block up to 30s without checking container pressure, despite keeping an unreaped source Popen alive. The supervisor now samples the existing cgroup guard, unchanged 256/512MiB thresholds, 64MiB headroom and web RSS guard at least every 1 second during retry sleeps. On configured BLOCK, reserve loss, OOM group or three consecutive unreadable probes, it can perform **one** expedited attempt using the already bounded child wait/TERM/KILL path. If still unverified, child and queued source slot stay reserved; subsequent emergency checks do not bypass the original 30s repeated-signal throttle. Tests use synthetic samples and a fake clock with no live database or source API. No production DB/checkpoint/credentials/formula changes. GitHub green alone is not proof of Cafe24 256MiB/512MiB operation: keep Draft/HOLD, do not merge main or deploy.


**4.1.277 ISOLATED REAP RETRY / LOG RATE LIMIT:** V4.1.276 could retry the full TERM/KILL path and log every 0.5 seconds after a permanently unverified child wait/poll. For each existing unreaped Popen, the supervisor now uses exponentially increasing 0.5→1→2→4→8→16→30 second retry delays; within 30s of a failed bounded signal attempt only a timeout=0.5s read-only wait/poll recheck runs. Another bounded TERM/KILL attempt is permitted no more than once per 30s for a continuously unresponsive process. Print retry diagnostics only at failure streaks 1/2/4/8/16/32 with safe exception type, never raw credentials/paths; limit worker-status poll-error logs to one per 60s. Process/queue slots, existing PostgreSQL checkpoints and source API quota remain protected; do not treat unknown termination as COMPLETE. Existing 256/512MiB cgroup thresholds, 112MiB child+64MiB reserve, 300s web grace, PG pool1+1 and budget/vendor formulas unchanged. Source-free fake-clock regression and CI gates are not actual Cafe24 deployment proof: keep Draft/HOLD.


**4.1.276 BOUNDED CHILD WAIT/REAP ERROR RECOVERY:** The isolated supervisor previously assumed that `Popen.wait()` and the post-TERM/KILL waits cannot raise OSError, ECHILD, MemoryError or RuntimeError. Such errors could orphan a still-running source child or yield a false source completion. The new bounded reap path retries at most 2 transient wait faults before failing closed: only the disposable source child is TERM'd, rechecked with max 2s wait, KILL'd only when needed, and rechecked with another max 2s wait. If neither wait nor poll proves terminal, the original child/queue stay reserved and the supervisor safely retries after 0.5s, without starting a duplicate source. An ambiguous zero exit is published as resumable memory HOLD, never COMPLETE; a verified successful wait still reports COMPLETE. A read-only status poll error is treated as still-running, not an HTTP 500. CI regressions cover simulated ECHILD/TERM/KILL and unreapable child status with no source calls, PG updates or data deletion. Original memory thresholds, web grace, data/checkpoints, 1+1 pool and advisory source lock unchanged. Draft/HOLD, no real Cafe24 deployment proof.


**4.1.275 UNREAPED CHILD SUPERVISOR REATTACH:** A child process may remain after its supervisor thread unexpectedly exits. V4.1.274 only repairs an orphaned queue when there is no Popen; it intentionally avoids discarding live or exited-but-unreaped children. Under the existing isolated-worker lock, V4.1.275 now checks Thread.is_alive(), starts exactly one lightweight reaper for the same existing Popen, and leaves child/queue/PG source admission intact. A reaper Thread.start OSError/RuntimeError/MemoryError is safely logged without secrets and retried after a 5-second delay; an exited-but-unreaped Popen is reported as reap_pending and prevents false batch completion. This does NOT signal/kill the source child, reset a checkpoint, spawn another source worker, or bypass 256/512MiB memory admission or PostgreSQL source locks. An independent new source generation resets stale reaper retry timing. Tests fake process timing without live external source APIs or production PostgreSQL; production Cafe24 proof remains pending. No main merge/deploy (Draft/HOLD).


**4.1.274 DEAD SUPERVISOR ORPHAN QUEUE HOLD RECOVERY:** In the isolated source scheduler, a non-None supervisor Thread reference was treated as running even after the thread had died. With no live child, a stale pending source could keep the auto cycle stuck without launching another worker. Status and request admission now check `thread.is_alive()` under the existing worker mutex. Only when there is no child and no live supervisor are orphaned pending shopping/budget sources marked resumable WAITING_MEMORY (in-memory only), the queue cleared, and the stale reference discarded. A live supervisor in its childless admission gap and an active child are never altered; manual/automatic retries still use original cgroup admission and source advisory locks. No SQL, source API, checkpoint mutation, schema DDL or memory threshold relaxation. Mocked deterministic regressions cover queue recovery/idempotence, live-gap preservation and resuming automatic scheduling. Draft/HOLD: not a substitute for Cafe24 integration proof.


**4.1.273 READ-ONLY CHECKPOINT ABI CUTOVER GATE:** Code-level comparison against live main 4.1.238 found identical app/shopping checkpoint DDL and budget table declarations/resume logic. The existing `scripts/g2b_cold_cutover_guard.py` does NOT validate the physical PostgreSQL checkpoint table schema. Before considering 4.1.238→latest migration, run the additional `python -B scripts/g2b_checkpoint_cutover_guard.py` using the **existing, unmodified production database credentials**, with `G2B_TEST_MODE=0`, `G2B_RUNTIME_ROLE=UNIFIED` and both legacy reset flags OFF. The new checker examines the expected `NORMALIZED_NO_RAW_V1` marker plus 14 mandatory columns and `(dataset, scope_key)` primary keys in both app and budget checkpoint tables. Dedicated one-use PG autocommit, session read-only, 2.5s SQL timeout, `LIMIT 1` sampling; no schema/data mutations, no API requests or historical scan. Return code 2 on incompatibility or inaccessible DB. This is a *separate* gate from actual old PID shutdown + advisory lease clearance and is not evidence of full source/page correctness. GitHub CI uses an ephemeral PG instance, not Cafe24. Keep Draft/HOLD and no deployment.


**4.1.272 HISTORICAL V4 SCOPE RESET RETIRED:** The former `v4_scope_migration.apply_v4_scope_reset(force=True)` could delete shopping/bid/service RAW and checkpoints, classifications, lifecycle rows, shopping state keys and result snapshots. Although no production startup call path was found, the old entry point is now an inert compatibility shim: returns `status=RETIRED`, `deleted={}`, `snapshot_cleared=False` and ignores force. No SQL, file operations, snapshot invalidation, migration marker writes or source API work. Historical module constants remain importable. Preserve all 4.1 PostgreSQL source, normalized history, checkpoint, receipt, budget/vendor rules and 256/512MiB isolated-worker 112+64MiB/300s grace/1+1 pool policy. All GitHub checks are development evidence only; do NOT merge main or deploy Cafe24 yet.


**4.1.271 NON-DESTRUCTIVE EXISTING STORAGE GATE:** Retire the historic fresh-start reset path entirely. Even when both legacy reset flags are set, a completed `NORMALIZED_NO_RAW_V1` marker returns SKIPPED without deleting any PostgreSQL schema or legacy SQLite file. When marker is absent and either existing workload schema or legacy SQLite is found, fail closed as `G2B_V41_EXISTING_STORAGE_PROTECTED` before any DDL. Inspect marker with read-only catalogs; only a completely empty database may receive additive workload schemas and release marker. Never DROP/TRUNCATE/DELETE existing business data, checkpoint, observation, revision, receipt or credentials. Synthetic PostgreSQL and source-free regression gates protect these paths. Existing 256/512MiB guards, 112MiB child +64MiB reserve, 300s boot grace, 1+1 SQL pool, source quota and business rules unchanged. Keep Draft/HOLD: not a Cafe24 production proof.


**4.1.270 INTEGRATION AUDIT / SHARED PG POOL FAILURE ISOLATION:** Cumulative Draft PRs #301–#330 were verified as an unmerged sequential stack. Budget migration/contract/index exceptions and `postgres_ready()` failures must no longer dispose/reset the canonical `g2b_database.engine()` Pool used by login, shopping and web menus. Only budget metadata is invalidated for a later bounded repair. Rebinding to a rotated canonical Engine prevents stale budget pool reuse. SQLite test-only disposal remains. Three source-free mocked-Engine regression cases guard these paths. No business rows, checkpoint tables, DDL definitions, external source calls, worker quotas, pool 1+1 limits or 256/512MiB 112+64MiB/300s admission rules change. PR remains Draft/HOLD; verify true Cafe24 cutover separately.


**4.1.269 EXPLICIT FORCE/KEY WAKE PRIORITY ACROSS AN ACTIVE AUTO CYCLE:** An operator force request or saved G2B/LOFIN API key arriving **while shopping/budget source work is already running** must not disappear when the scheduler's subsequent sleep routine captures the current revision and clears its Event. The automatic scheduler now snapshots explicit-wake generation before each source cycle and compares it against the current generation when the cycle drains. Any in-flight explicit requests coalesce into **one** additional serialized batch, still gated by existing 256/512MiB memory admission, 112MiB+64MiB reserve, source advisory locks, quotas and 300s web grace. A source-terminal-only notification never triggers a redundant immediate batch; it only rebases the next safe timer. Explicit and terminal signals arriving together always prioritize the explicit operator/key intent. No SQL/source API work is added to the notification path, and no PostgreSQL data/checkpoints or business formulas change. GitHub regression CI does not establish Cafe24 real 512MiB collection; HOLD merge/deploy until actual cold-cutover/headroom/HTTP/source admission checks.

**4.1.268 MANUAL SOURCE COMPLETION WHILE AUTO BACKOFF SLEEPS:** The lightweight UNIFIED automatic scheduler may wait 60–900s for a failed memory admission. An interactive shopping/budget worker completing successfully during that window previously could not rebase the already sleeping retry and caused needless immediate replay when the old deadline expired. Every isolated terminal publication now advances a source-completion revision under the existing status lock and signals the scheduler only after publishing run-state, error, aggregate and memory streak. A terminal-only wake causes recalculation of the remaining sources' actual COMPLETE/WAITING_MEMORY/WAITING_QUOTA statuses and resets the countdown; **it does not start source collection**. An explicit operator force or saved G2B/LOFIN key uses a separate revision and still wakes immediately. Event clear is serialized against both counters to prevent losing a completion between delay computation and sleeping. No PostgreSQL lookup, source API, extra web DB connection, new worker or checkpoint mutation in the wait/signal path. Existing 256/512MiB cgroup guard, 112+64MiB reserve, 300s web-first grace, 1+1 connection pool, advisory leases/watchdog, quotas and resumable business data preserved. GitHub selfchecks are not Cafe24 512MiB operational confirmation. Keep Draft/HOLD and cold-cutover gates.

**4.1.267 MANUAL/AUTO MEMORY-HOLD INTERLEAVING:** An interactive source submit may fail a slow cgroup admission probe just as the automatic scheduler starts the same or another child. No stale HTTP HOLD may overwrite that worker's RUNNING/QUEUED. After failed admission, recheck current child/pending under the existing isolated-worker mutex: same source is ALREADY (no state mutation); different active source queues safely; only a truly idle source publishes in-memory WAITING_MEMORY. A pending source remains reserved even while the supervisor temporarily has no active process during an out-of-lock admission probe. If that supervisor probe fails after a manual worker starts, do not publish an obsolete WAITING_MEMORY; supervise the active same-source child, or requeue the other source. Parent launch HOLD is in-memory only and does not open PG while locked; the lifecycle regression explicitly asserts lock ownership and a separate test asserts no historical source-error DB reads. No change to 1-child serial execution, 256/512MiB 112+64MiB, 300s web-first grace, SQL pool 1+1, PG leases/watchdogs, source API caps, 60–900s memory retry, durable checkpoints or business rules. Github CI scenarios are not Cafe24 512MiB operational proof; keep rollout HOLD.

**4.1.266 DISPOSABLE WORKER STATUS HANDOFF:** A completed shopping/budget child remains reserved until its terminal state is published. A simultaneous same-source manual request cannot start the next process early; different-source requests queue until the previous terminal state is committed to the in-memory status. STARTED/QUEUED HTTP callbacks publish only after checking live child identity / pending membership under the isolated-worker mutex; promoted/finished children cannot be demoted or resurrected as RUNNING. Exit state and source aggregate update atomically under the status lock. No DB or source HTTP calls are made while holding the worker mutex. The original memory cgroup 256/512MiB limits, child estimate 112MiB plus 64MiB reserve, first-boot 300s web grace, advisory locks, API limits, source checkpoints/receipts and calculations remain unchanged. GitHub simulation is not Cafe24 512MiB operational validation. Keep deployment HOLD and main unchanged.

**4.1.265 QUEUED SOURCE FAILURE RECOVERY:** A failed memory snapshot or subprocess fork (OSError including ENOMEM/EAGAIN) when starting a serialized isolated shopping/budget child is a non-destructive HOLD. In particular, a failed queued child after its predecessor exits must not crash the supervisor thread and leave the automatic scheduler indefinitely waiting for `supervisor_running`. Pending source states become `WAITING_MEMORY`, supervisor/process references clear, and the parent does not read historical DB source errors merely to report an admission/spawn HOLD. Repeated parent launch HOLD uses the existing 60/120/240/480/900-second capped memory retry backoff, preserving checkpoint/resume rules. No new DB connection, network source call or data rewrite is performed in these exception paths. The same safe HOLD applies to initial manual source starts and background match-worker launches. Keep 64MiB headroom reservation, 112MiB forecast, 256/512MiB cgroup rules, 300s web grace, original pool and API quotas unchanged; GitHub CI is not the real Cafe24 512MiB runtime. No main merge or Cafe24 deployment.

**4.1.264 LARGER-CGROUP RUNNING-CHILD RESERVE:** At 512MiB the original cgroup BLOCK threshold is 460.8MiB effective (51.2MiB remaining) even though isolated source admission explicitly reserves 64MiB. The parent now also preempts ONLY its disposable worker when the remaining effective 512MiB+ cgroup memory is <=64MiB (448MiB used at 512MiB), preserving the 64MiB reserve while a source child runs. The original 256MiB BLOCK at 224MiB, existing underlying BLOCK thresholds, 112+64MiB source admission, web 300-second grace, PostgreSQL advisory locks, API limits, data/checkpoint/receipt retention and all calculations are unchanged. CI additionally observes a source-free child touching 48MiB in real RAM while holding a shopping advisory lease, 12 concurrent authenticated menu requests, /live and /health latency, PostgreSQL source session and OOM counters. It does not establish actual Cafe24 512MiB cgroup performance; keep deployment HOLD until real cold cutover and operator-verified production headroom.

**4.1.263 BOUNDED MENU WAIT:** Keep original 2 simultaneous read admissions and SQLAlchemy QueuePool 1+1. Only normal HTML navigation GETs may wait for a slot for at most 0.6s; at most 4 waiting asyncio tasks, no additional DB checkout or synchronous thread. API polling and XLSX export still reject full slots immediately; over-limit or timed-out navigation returns safe 503 with Retry-After. Waiter cancellation releases its queue ticket; /live, /health, /ready remain ungated. Existing cgroup 256/512MiB admission 112+64MiB and worker/checkpoint/PG source leases remain unchanged. GitHub CI additionally holds a shopping source advisory lease in a disposable source-free child process while 12 authenticated HTML/API GET requests compete for the unchanged web pool and /live, /health remain reachable. This cannot prove a full real source page collection on Cafe24. CI tests do not prove Cafe24 live collection; release HOLD pending cold cutover and actual combined source/menu memory observation.

**4.1.262 WEB/POSTGRES POOL SINGLETON GUARD:** Concurrent first visits to dashboard, settings, shopping, budget and vendors can race during lazy `g2b_database.engine()` creation. Per-process RLock serializes pool construction and cache reset; a failed replacement does not dispose the existing engine. This is NOT a change to the default 1+1 SQLAlchemy checkout limit, 2-menu nonblocking admission, separately pooled isolated worker, or PostgreSQL advisory lease. CI 10-thread real PostgreSQL test and 12-thread synthetic race test must pass. This does not prove real Cafe24 512MiB parent/child peak memory or eliminate deliberate HTTP 503 under admission saturation. Keep 4.1.238->new cold-cutover and postboot gates, no main merge/deploy before operator check.

**4.1.261 RUNNING-CHILD CGROUP TELEMETRY FAIL-CLOSED:** On both 256MiB and 512MiB production UNIFIED tiers, if an already-running isolated shopping/budget child loses a usable cgroup limit, effective usage or BLOCK threshold reading, parent immediately treats the incomplete sample as `CGROUP_MEMORY_PROBE_UNAVAILABLE` and terminates/reaps only the disposable child (SIGTERM, then bounded SIGKILL). Source becomes `WAITING_MEMORY`, preserving PostgreSQL checkpoints and normal retry backoff. A healthy cgroup reading still uses the unchanged BLOCK threshold; 112MiB child forecast + 64MiB reserve and 300s web grace are NOT relaxed. GitHub mock tests cannot verify actual Cafe24 memory/HTTP behavior; keep deploy HOLD and follow the cutover and postboot gates below.

**4.1.260 256→512MiB web/source separation:** Memory upsizing does NOT permit shopping or LOFIN source collection in the HTTP web process. All production UNIFIED source-heavy work uses one serialized disposable child with existing 64MiB reserved admission, source-specific advisory leases, and watchdog; a 512MiB cgroup with default 112MiB worker requires observed effective memory STRICTLY below 336MiB before launch (256MiB stays strictly below 80MiB). The HTTP process retains 300 seconds of source-free first-boot grace at both tiers. During the child lifetime, parent checks cgroup BLOCK state / 512MiB limit and its own RSS every 0.5 seconds and preempts only the child if unsafe; no DB/keys/source quota modification. Run `python -B scripts/g2b_postboot_collection_admission.py --web-grace-elapsed` from the SAME warmed web cgroup at 512MiB as well, confirm report mode `ISOLATED`, admission allowed, and then verify bounded real source fetch/checkpoints and `/live` separately. Do not claim actual Cafe24 pass based on CI mock/70s RSS. If guard returns HOLD do not bypass reservations. Existing 4.1.258 cold-cutover requirements apply unchanged.


**4.1.259 POST-BOOT AUTOMATIC COLLECTION ADMISSION GATE (mandatory for operational acceptance):** A GitHub 70-second /live test or a running Cafe24 dashboard is NOT evidence that automatic G2B/LOFIN collection can ever start. With the unchanged 256MiB limit, default 112MiB worker budget and 64MiB reserve, the cgroup effective memory **must be strictly below 80MiB** at child launch; otherwise the child guard returns CGRP_CHILD_HEADROOM_UNSAFE / WAITING_MEMORY. GitHub web RSS ~92–94MiB is a different measurement from production cgroup effective memory and cannot alone confirm actual Cafe24 capacity. **Do not silently relax the 64MiB reserve or process soft limit to force launch; avoid 502/OOM.** After the separate 4.1.258 cold cutover gate and live startup, verify actual deployed SHA, /live, /health, /ready, and 300s web-first grace elapsed on the real Cafe24 application. Then from the *same web container/cgroup* with unchanged environment execute `python -B scripts/g2b_postboot_collection_admission.py --web-grace-elapsed`. A sanitized JSON report states current cgroup limit/effective, worker requirement and whether source admission is safe; exit2 / `AUTO_COLLECTION_WORKER_MEMORY_ADMISSION_HOLD` means **operational rollout acceptance HOLD**, even if /ready=200. The grace flag is an operator attestation, not machine-verified uptime. Once admitted, separately verify one actual bounded G2B/LOFIN collection cycle, checkpoints, UI, memory and OOM count; admission alone is not collection proof. If 256MiB cannot fit the guarded child, evaluate a larger Cafe24 memory tier or separate collection instance/worker after approval—not unsafe threshold relaxation. This script never reads/writes business PostgreSQL or source APIs.


**4.1.258 OPERATOR COLD-CUTOVER GATE (pre-deployment HOLD):** Do NOT overlap old 4.1.238 web or `g2b_heavy_worker` processes with new 4.1.258 on the same PostgreSQL. Previous idle-in-transaction 60s lease timeout can release old advisory locks while its source worker still runs. (1) STOP/terminate old G2B app and all old shopping/budget/match workers on every relevant Cafe24 instance. (2) Confirm PIDs are gone from the actual Cafe24 process list/logs, not just `/ready` or a successful GitHub CI. (3) Keep `G2B_V41_FRESH_START=0` AND `G2B_DESTRUCTIVE_RESET_CONFIRM=0`; do not delete/initialize app/budget data/keys/checkpoints. (4) From the *new* checked-out code with the same production PG connection configuration, run `python -B scripts/g2b_cold_cutover_guard.py --old-processes-stopped`. It reads advisory-lock state only; it never runs DB initialization/DDL or source APIs, prints a sanitized report, and returns nonzero until the old-stop operator attestation AND PG global/shop/budget locks all pass. Never interpret `--old-processes-stopped` as remote PID verification; the operator must actually confirm shutdown on all instances. (5) Start only the new web app and verify build SHA/PID, `/live`, `/health`, `/ready`, login/menus, 256MiB cgroup memory, 300s web-first grace, then automatic resume from preserved `RUNNING` checkpoints. GitHub Actions has no access to real Cafe24 PIDs or production DB. If PID confirmation, connection or advisory lock check is unavailable, keep deployment HOLD and do not attempt overlapping rolling deployment.


**4.1.257 MIXED-VERSION DEPLOYMENT HOLD (mandatory):** main 4.1.238 uses SQLAlchemy engine.connect with implicit transaction-scoped session and `idle_in_transaction_session_timeout=60000` while holding session advisory locks. The lock can be released by PostgreSQL while the legacy collector still performs slow source I/O. Its independent source worker may then overlap with a newly deployed 4.1.257 collector. The updated disposable PostgreSQL autocommit watchdog protects *new* children, NOT the older still-running 4.1.238 process. Before merging any PR stack and deploying from main, stop/terminate the old application and its `g2b_heavy_worker` child processes, confirm they have exited, keep `G2B_V41_FRESH_START=0` and preserve all DB rows/checkpoints/keys, then launch the new release (cold nonoverlapping transition). Verify real Cafe24 PID/HEAD, `/live`, `/health`, `/ready`, database advisory lock activity, parent/child RSS and OOM counts, and authenticated shopping/budget views before enabling collection. Do not use an unverified rolling overlapped deployment from 4.1.238. GitHub CI is not Cafe24 live verification.


**4.1.238 configuration first-paint optimization:** `GET /settings` no longer synchronously computes full readiness or calls schema/index-inspecting `budget_storage.storage_ready()`. It reads cached backend/PG results and one small SELECT for previously saved G2B/LOFIN probe metadata. It preserves all credential-save, delete and explicit API probe POST routes, and displays a link to explicit `/ready` for detailed database readiness. Watch `G2B_SETTINGS_RENDER_MS` for actual authenticated page latency on Cafe24. A cached status is NOT proof the PostgreSQL schema has been freshly checked. Preserve database rows, checkpoint/receipt/revision history and `NORMALIZED_NO_RAW_V1`.


**4.1.237 menu/OOM recovery:** dashboard automatic first-load `/api/dashboard-summary` no longer loads all budget current hash keys and classifications into the web process. Use SQL grouped exact-current target counts; no DDL on shopping count reads; budget total counters grouped and bounded. No PostgreSQL reset, no data/cache deletion, no source API calls. This addresses a confirmed unbounded-memory code path, not a proven Cafe24 OOM event. Verify production /live, /health, /ready and interactive menus plus exit/OOM logs.


4.1.236 emergency web-first: with a visible 256MiB cgroup, automatic collection is deferred for five minutes after startup. Workers are admitted only if predicted child memory + 64MiB buffer fits; queued workers use the same guard. If the cgroup size is unknown, isolate and hold rather than run source work in the HTTP process. Existing data/checkpoints are preserved and no operator env edits are needed. WARNING: source collection may be WAITING_MEMORY on the 256MiB tier. To prove a live fix, validate actual Cafe24 deployed SHA and 502 behavior before and after five minutes; GitHub CI does not perform Cafe24 deployment.


4.1.235: dashboard and settings readiness use bounded PostgreSQL exact-current classification counts, rather than loading all budget hashes and classification rows into web RAM. Database data, checkpoints and receipts are preserved. GitHub CI is not a Cafe24 production deployment.

4.1.234 read-path recovery: GET /budget must use read-only table metadata (no schema/index migration on a page request); budget SELECTs in UNIFIED/RESULT_SERVER have transaction-local 3-5.5s timeout and 4MiB work_mem. High 256MB cgroup pressure returns an authenticated safe HTML notice rather than attempting heavy budget work. This update does not reset PostgreSQL, existing NORMALIZED_NO_RAW_V1 marker, checkpoints, receipts or revision records. Validate actual Cafe24 process and deployed SHA separately from CI.

This runbook is the deployment handoff for **SINSUNG G2B VNEXT 4.1.296** (stacked audit branch only; 4.1.238 Cafe24 live check pending).

**4.1.217 original rollback snapshot:** full functional repository files from SHA `97c8b5a0743fe74434ccb4af6694a4ffa423ce9c`; only seven release metadata/documentation/CI labeling files differ. GitHub restore does NOT revert Cafe24 deployment, environment variables, runtime Python dependencies, cgroup limits or PostgreSQL rows. Keep fresh-start/reset disabled; preserve `NORMALIZED_NO_RAW_V1`, checkpoints, revisions and receipts. On 502 after initial HTTP availability, inspect Cafe24 runtime exit reason/OOM, deployed commit, PORT and health failures before another rollback.

Historical 4.0-to-4.1 planning once considered replacing older G2B data. This is
not an operational instruction: in 4.1.281, all existing PostgreSQL business
records, checkpoints, receipts, revision history and settings must be preserved.
Destructive historical reset entry points are retired and cannot be re-enabled.

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
USAGE, app-schema CREATE, and the required table DML privileges. An entirely empty new PostgreSQL installation may add new schemas once, but
existing workload schemas and markers must never be dropped or reset.

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
# Legacy compatibility value. Production UNIFIED auto-collects regardless of 0.
G2B_AUTO_SYNC=0
# Emergency kill-switch only. Keep 0 for normal automatic operation.
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

## 4. Retired historical destructive fresh start — DO NOT RUN

The old 4.0→4.1 procedure that removed workload schemas and legacy SQLite
is **disabled in 4.1.271**, including when both legacy reset environment
flags are enabled. Preserve all existing G2B PostgreSQL schemas, data and
checkpoints. The production bootstrap reads the release marker without DDL.

- Completed marker `fresh_start_4_1_0=NORMALIZED_NO_RAW_V1`: read-only
  verification, `SKIPPED`, no legacy file cleanup.
- Missing/unknown marker and pre-existing workload schema or SQLite file:
  fail closed; do not clear data or create a replacement marker.
- Truly empty PostgreSQL with no legacy SQLite: additive first-time schema
  and marker creation only; no existing schema recreation or source API calls.

For cold cutover, both deprecated flags must be OFF. If a completed marker
is missing, stop deployment and inspect the PostgreSQL records separately;
never try to force recovery by enabling destructive flags.

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

Current owner policy (4.1.192+) starts recurring source work automatically on production `UNIFIED` after backend/schema readiness. No manual collection click and no positive `G2B_AUTO_SYNC=1` flag are required; a legacy `G2B_AUTO_SYNC=0` value does not disable UNIFIED recurrence. `G2B_AUTO_SYNC_DISABLE=1` is the explicit emergency kill-switch and always wins. On the 256 MiB tier, the scheduler remains lightweight while source-heavy work is serialized through disposable isolated workers; new heavy work is held when process/cgroup memory pressure reaches the guard thresholds.

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

For a normal current UNIFIED redeploy, recurring collection resumes automatically after backend/schema readiness. A legacy `G2B_AUTO_SYNC=0` may remain in the environment without disabling UNIFIED automatic collection. Confirm `G2B_AUTO_SYNC_DISABLE` is absent/0 for normal operation; set it to 1 only for an explicit emergency stop.

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

Current 4.1.281 preserves all already stored PostgreSQL data and related
checkpoints; historical 4.0-to-4.1 replacement plans no longer authorize any
delete, reset or purge during deployment.

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
As of 4.1.271, **neither flag nor their combination can delete or reset existing
PostgreSQL/SQLite storage**. The cutover gate still requires both to be OFF.

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
