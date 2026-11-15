# G2B recovery generation: LOCAL candidate, staging only

## Release boundary

Base: owner-private official Site v35, commit `d10c85c122c959f5830aa2fc1dda0524d5bd729d`.
This candidate has not been deployed, enabled, or used to call a real provider. No scheduler, production setting, existing session, diagnostic latch, source identity or current read-model row was changed. All provider responses used in tests are synthetic.

This is a new staging-only recovery lane. It does not resume the old collection session, erase its error, make an authoritative snapshot, or publish rows to current delivery views. There is no promotion endpoint. An observed two-pass match ends in `observed_consistent_needs_review`, with all of these false:

- `sourceCompletenessVerified`
- `pointInTimeSnapshotVerified`
- `canPromote`
- `legacyResumed`

The target scope is exactly the predecessor's failed query date, not its entire historical date range. For the currently reported predecessor this is 2026-11-05. The original 570 records remain in the existing views; newly staged candidates do not appear there.

## Source contract and accuracy limits

The public operation specification at https://www.data.go.kr/data/15129471/openapi.do documents pageNo/numOfRows, date and request/contract/product filters, but no snapshot token, stable sorting guarantee, cursor or response revision. Its catalog labels updates real-time: https://www.data.go.kr/catalog/15129471/openapi.json . The documented identity fields do not themselves promise uniqueness of `(dlvrReqNo,dlvrReqChgOrd,prdctSno)`.

Therefore overlap means a repeated current identity, not proof that ordering changed. Bounded conflict evidence records first/current page, SHA-256 identity and payload hashes, whether payloads match, and conflict count. Raw source values, secrets and arbitrary exception text are not logged or returned as conflict evidence. Identity-contract verification remains false. This release does not invent new identity fields, filters or sorting parameters.

Each of exactly two passes requires contiguous pages, the same total and page size, exact row counts, no duplicate current identities, and unique source count equal to reported total. Pass 2 additionally compares ordered identities and canonical full source-payload hashes against pass 1. A match demonstrates observed consistency only. Both passes could still miss source changes invisible between reads. Source totals include all categories; target/excluded counters reconcile separately. Query-date fallbacks remain distinguished from source dates; no authoritative receipt-date completeness is claimed.

Parsed canonical JSON includes unknown fields, preserves types and array order, and rejects unsafe numbers, unsupported objects/accessors, excessive structure and identity truncation. Network parsing first checks the complete bounded JSON grammar and rejects duplicate decoded object keys (including escaped aliases), invalid UTF-8 and numeric lexemes that would lose significant precision or underflow. Conservative rejection is intentional. Every present identity alias must be a string; numeric or null identity representations stop conservatively. This representation has not yet been verified against a new live response. Numeric checks are conservative bounds/significant-digit screening, not arbitrary exact-decimal arithmetic. Canonicalization is not a source-signed representation.

## Persistence and ownership

An additive migration creates only six tables:

- g2b_recovery_generations: unique root session, exact diagnostic binding, frozen manifest and metadata snapshot, cursor, counters and stop state.
- g2b_recovery_runs: durable owner, 90-second deadline, eight-attempt counter and in-flight latch.
- g2b_recovery_attempts: unique generation/pass/page; reservation, quota date, result and failure evidence.
- g2b_recovery_pages: committed page receipts and ordered/raw response hashes.
- g2b_recovery_source_rows: pass-1 identity, ordinal, payload hash, exclusion and source-date basis.
- g2b_recovery_target_rows: separate normalized target payload/provenance.

Creation requires an eligible, unchanged error predecessor and its completed v2 `SOURCE_PAGE_OVERLAP` diagnostic, which in turn references the completed source-phase first probe. One root can have only one generation, including after failure. It cannot be recreated under a changed snapshot timestamp.

The predecessor fingerprint is produced by ONE consistent SQL statement and fences every reservation and page transaction. It includes all session columns, metadata counts/checkpoint consistency and exact diagnostic/probe rows. It is explicitly **not** a full content digest of every historical source/delivery row. It cannot detect an unrelated writer changing old payload content without changing the checked metadata. The candidate itself never writes those tables. Local tests separately snapshot all pre-existing tables before/after, including both diagnostics and 570 seeded delivery rows, and compare their complete ordered row serializations. `usage` is intentionally excluded from that equality because it is the shared reservation ledger.

The old generic session stays an immutable predecessor. This successor owns only staging collection. Current search, summaries, matching, dataset epochs and all existing read models remain unchanged.

## Boundaries enforced

- One failed date; page size 999; at most 20,000 source identities.
- At most 21 pages/pass, exactly two passes, at most 42 unique attempts/generation.
- At most 42 durable runs/generation; no unlimited empty runs.
- At most eight page attempts per run, enforced by SQL and runner loop.
- One provider request at a time; each page commits before the next reservation.
- One fixed 90-second run deadline, including run preflight and request body time when invoked through the endpoint.
- Do not start a new page with less than 30 seconds left. Source+body timeout <=25 seconds and leaves a 5-second storage reserve.
- At most 8 MiB response; 64 MiB cumulative pass-1 staging payload estimate. This estimate is not a physical database-size guarantee.
- At most 1.4 MiB per JSON binding and 64 statements per page transaction. Oversized pages fail closed with no partial visible page.
- No automatic third pass, retry, new generation, error clearing, duplicate skipping, refund or quota pool.

The runtime bounds asynchronous waits and uses an execution-time database-clock deadline assertion on reservation, page-commit and run-finish admission. Already submitted database operations cannot be forcibly cancelled, and synchronous JS parsing/canonicalization cannot be preempted. A delayed write acknowledgment is uncertain, not evidence of non-execution. Production clock skew and resource limits remain operational considerations. An active/uncertain run has no expiry-based takeover. A transaction admitted just before the deadline may finish after it; the deadline fence prevents expired admission, not physical transaction preemption. A zero-attempt run cannot be acknowledged finished: it stops uncertain.

## Quota and stop semantics

Reservation is one atomic transaction: exact state/root fence, existing KST `usage` cap check, unique attempt insert, usage increment and run/generation counters. There is no separate recovery allowance. Budget must be configured in 1..950. Provider calls made by other applications outside this ledger are not observable; existing account/service allocation review is still required.

KST is checked again after the last awaited preflight immediately before dispatch. A rollover burns the old reservation conservatively and stops without a source call. Pause/cancel increments a control fence and prevents the in-flight page from committing; the already-issued request may have consumed quota.

Any source, validation, quota, storage or uncertain-write failure stops. Catch paths only preserve bounded error/evidence and read back the same IDs. Failed and uncertain attempts cannot be retried. A known successful page commit whose acknowledgment was lost remains available in readback, but the run still requires review; the code does not continue automatically. If the initial run-creation transaction cannot be acknowledged, there is no source dispatch, and the code attempts to mark the generation uncertain. If storage is entirely unavailable, that marker itself cannot be guaranteed; absence of a durable record must not be treated operationally as permission to retry. No arbitrary error strings are returned.

Pause/cancel controls are supported. There is deliberately no resume/reset/retry control in this first candidate. Disabling the gate blocks future writes/calls and does not alter preserved data. A gate check also runs before commit; already submitted atomic commits may finish and require same-ID readback.

## Disabled configuration and private API

All mutation endpoints are disabled unless **all** exact settings match:

- `G2B_GENERATION_RECOVERY_ENABLED=approved-staging-only`
- `G2B_GENERATION_RECOVERY_ID=<reviewed generation UUID>`
- `G2B_GENERATION_RECOVERY_ROOT_ID=<exact predecessor session UUID>`
- `G2B_GENERATION_RECOVERY_DIAGNOSTIC_ID=<exact completed v2 diagnostic UUID>`

Existing approved source readiness and shared daily budget settings are also required. No defaults enable this feature. Do not change the old probe gates/latches, legacy session, collection scheduler or generic resume controls. The frozen scope/version/endpoint/query/limits manifest must match at each source reservation and page commit. Stop/control and uncertainty-audit writes intentionally remain available without that data fence. Date-window configuration changes stop rather than silently widening scope.

Private endpoint: `/api/collection/generation-recovery`.

Read-only, also available while disabled:

`GET /api/collection/generation-recovery?id=<generation UUID>&runId=<run UUID>`

Create after separately reviewed publication/activation:

```json
{"action":"create-staging","generationId":"<generation UUID>","rootId":"<predecessor UUID>","diagnosticId":"<v2 diagnostic UUID>"}
```

Start one bounded run with a NEW run ID only after the previous run was acknowledged finished and the generation is ready:

```json
{"action":"run-staging","generationId":"<generation UUID>","rootId":"<predecessor UUID>","diagnosticId":"<v2 diagnostic UUID>","runId":"<run UUID>"}
```

After an ambiguous response, use GET with those same IDs. Reposting the same run ID is readback-only; do not invent another ID to evade uncertainty. Stop on any error/paused/cancelled/uncertain result. Do not interpret HTTP200 alone as collection success; inspect generation/run state and error. A successful eight-page boundary is `generation.state=ready` and `run.state=finished`, not verified completion.

Pause/cancel uses the same exact three IDs and `action: "paused"` or `action: "cancelled"`. Cross-origin POST, non-JSON, malformed or >2048-byte bodies and unknown actions fail before dispatch. The existing owner-private Site access boundary remains required; the route is not a new public authentication mechanism.

## Deployment prerequisites and rollback

Publication is NOT part of this work. A reviewed deployment would need the additive migration before enabling the new endpoint. Verify the exact candidate manifest, migration, compiled build, private access, unchanged predecessor/diagnostics and intended gate IDs first. Initial rollout must remain disabled. Existing diagnostic gates remain untouched.

To stop: disable only the new generation gate and read the same IDs. To roll back code: restore v35 while leaving the additive recovery tables/audit evidence intact. Do not drop recovery tables, reset attempts, decrement `usage`, clear legacy errors, or copy staging rows into delivery_rows. A rollback cannot undo a provider call or release a consumed permit. No current read-model pointer was changed, so there is no published staging dataset to reverse.

If the source identity/snapshot contract remains unconfirmed, no later code should auto-promote these observations. Full source replacement/deactivation needs a separately reviewed source guarantee and read-model/version strategy.

## Validation record

See `outputs/` logs and the final parent report for exact final counts/hashes. Local tests use only synthetic responses. Unit SQLite concurrency tests model interleavings; they do not alone establish physical D1 behavior. Independent local Miniflare/workerd checks additionally exercised a 1,399,966-byte multibyte JSON binding, a 64-statement transaction, assertion rollback, and four concurrent quota reservations at949 ending950 with exactly one successful reservation. External network was explicitly denied in that check. These are local emulator results, not a guarantee of production latency or quota allocation.
