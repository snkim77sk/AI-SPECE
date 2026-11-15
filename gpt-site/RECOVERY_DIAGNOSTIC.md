# G2B stopped-page diagnostic candidate

## Verified live state, 2026-11-08

The owner-private Site remains version 33, source commit `2dbbc20484dec32c3fc9c796e3bb3af0de7875ff`.
Session `ccf3b685-fc5d-4416-b04a-66cd68da3307` is stopped with `STORAGE_OR_SOURCE_FAILED` at 2026-11-05 page 11.
The read-only recovery endpoint returned HTTP 200, `metadataConsistent: true`, `pageCommitState: NOT_COMMITTED`, 22 committed receipts, 20,724 source identities, 570 target rows, and 23 decoded-response counts. The KST 2026-11-08 usage table contains 34 attempts. The stopped page is not committed; this does not identify the original failure cause. Recent official error-log retrieval returned no events.

This work made no live provider call, collection write, environment change, publication, audience change, or schedule change. Existing rows and the error remain in place.

## Why another diagnostic is needed

The failing version converted unclassified exceptions to a generic error and did not save the failing page payload. Version 33 improves future diagnostics but cannot reconstruct that lost exception. An offline fixture cannot prove whether the real page contains a duplicate identity, a malformed value, or a storage-specific failure.

The new candidate is a separate, operator-approved diagnostic lane. It does not resume the collector or clear its error. It is disabled unless `G2B_DIAGNOSTIC_PROBE_ENABLED=approved-once`, and also requires the existing provider key, approval, date scope and reviewed allocation gates.

## Attempt and preservation contract

- Only the exact live, consistent, uncommitted legacy generic-error snapshot is eligible. Paused, cancelled, running, or known-error sessions are rejected.
- One durable attempt latch is allowed per session/day/page/failure timestamp, independent of request ID. Duplicate requests only return the existing latch/result.
- Latch and one KST daily usage permit are reserved in one atomic batch. No source call follows an uncertain reservation response. If KST day changes before dispatch, the consumed latch is retained and no source call is sent.
- The source is requested at the same day/page/page size with redirects forbidden. Parsing, normalization, prior-page overlap, and construction of a commit plan are checked. The commit plan is never executed.
- Collection sessions, receipts, checkpoints, source identities, delivery rows, epochs and the original error are unchanged by this lane. Only its audit row and daily attempt counter are written.
- Results contain only allowlisted error code, stage, optional counts/response hash and preservation status. Credentials, URLs with secrets, raw rows, SQL and exception messages are not returned or logged.
- Source validation success is `SOURCE_VALIDATION_PASSED_STORAGE_NOT_TESTED`, not proof of successful storage and not permission to retry. Every result keeps automatic retry disabled.
- A reserved result with no final outcome is uncertain. Read it back using the same request ID; never invoke a new attempt or reset the latch.

## Proposed execution after explicit approval

1. Preserve owner-private access and confirm the current publication/source has not changed. Use the official Sites source and publication workflow for this existing project.
2. Publish the reviewed candidate with its generated schema-only migration and with the probe gate disabled. Confirm the new version and read-only state. No provider request is needed for deployment checks.
3. Enable only the temporary probe gate, retaining the existing daily quota and all source settings. Redeploy the same verified source as required by Sites configuration changes.
4. Save one request UUID in the operator's task record before issuing exactly one `POST /api/collection/source-probe` with `action: diagnose-once`, that request ID, and the failed session ID. Keep source/service secrets only in supported secret/header flows.
5. Regardless of request outcome, use `GET /api/collection/source-probe?id=<same-request-id>` and the existing recovery endpoint to inspect what happened before any other action.
6. Restore the probe gate to disabled through the supported flow. Confirm collection error/checkpoint, target counts, audit status and quota usage. Do not claim collection recovery or resume automatically.
7. Choose the actual repair from the diagnosis. Source overlap requires an explicitly reviewed non-destructive generation strategy. A clean source probe leaves actual D1 write behavior untested. Any future collection restart remains a distinct reviewed operation.

No production execution in this list has been performed by this candidate work.
