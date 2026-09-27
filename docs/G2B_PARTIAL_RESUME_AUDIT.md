# Partial collection resume audit — 2026-09-24

Baseline feature: `75cbd0f701360aba0b57036c936e7b512d1b8c6b`.
Main remains `ba866bf6ff4422726940d8d51518a85cbc060ea6`; PR #8 stays draft.

## Reproduced defect

An overlapping page could preserve a changed RAW revision and stop INCOMPLETE.
Resuming from the old page counter then returned COMPLETE even though the earlier
page receipt no longer matched current RAW. A partial checkpoint with a mismatched
source total or fetched counter could also finish before all pages were collected.
Six new regression cases failed on the baseline.

## Repair

The shared vNext collector validates both complete checkpoints and partial resume
prefixes against contiguous page receipts, per-page identities and hashes, current
RAW and immutable revisions, page size, cumulative source total, and fetched/saved
counts. Terminal reasons are checked against the recorded page sequence.

An invalid partial prefix starts a new generation at page 1 within the caller's
existing page budget. RAW, revisions, and earlier generation receipts are retained.
Valid interrupted prefixes continue at their next page; a verified COMPLETE
checkpoint remains reusable without a source request. Explicit `resume=False`
resets progress even if its first request fails.

## Verification

`tests/test_budget_partial_resume_vnext.py` covers the changed overlapping RAW,
five checkpoint-field mismatches, failed explicit replay, and valid retry after
transport failure or premature empty response. Run `python scripts/g2b_verify.py`
with the committed candidate SHA in `G2B_VNEXT_SOURCE_COMMIT_SHA` (or CI's
`GITHUB_SHA`); the existing report-semantics regression requires runtime identity.
All tests use disposable SQLite, empty source credentials, and blocked sockets.

These are local code and regression checks, not live collection evidence.
Production files, scheduler, bulk historical, APPROVED_HISTORICAL, and education
live transport remain untouched and on their existing HOLD boundaries.
