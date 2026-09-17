# G2B + LOFIN budget integrity repair — 2026-09-17

## Scope and hold

Source audited: `1bad48bd3199e322cef62fc64300e0ea35e71ea1`.
This repair is isolated from main, production schedulers, NO1, and DNR.
The feature branch moved during this work; preserve concurrent changes and review
this candidate against that branch before integration. Do not force-push or merge main.
Passing a synthetic regression or a one-page probe does not approve bulk collection.

## Response and collection

- Strict G2B and LOFIN JSON/XML envelope and result-code validation.
- Absent totalCount/list_total_count is `None`, never page length.
- HTTP errors, malformed HTML/JSON/XML, and malformed rows are not empty success.
- LOFIN INFO-200 is distinct from authentication, network, and schema errors.
- Positive totals require exact count equality; early empty, changing total,
  repeated/overlapping identities and oversized pages stop INCOMPLETE.
- Full unknown-total pages continue. Short/empty unknown-total termination follows
  the source paging contract, records its reason, and is not an external archive guarantee.
- `vnext_collection` atomically commits RAW, revisions, identity receipts and next
  checkpoint. Failed checkpoint writes roll back all of that page's changes.
- Resume fixes page size and query fingerprint. Concurrent checkpoint changes abort.
- Old COMPLETE checkpoints without receipt proof replay from page 1; RAW is kept.
- Receipt verification checks continuity, unique counts, next page, terminal reason,
  page size and existence of each saved revision. No silent counter double-counting.
- Detected anomalous dict rows remain in RAW, but cannot establish completeness.

## Budget-specific safeguards

The implemented source is QWGJK, not every national/local budget API. Each audited
unit is an explicit fiscal year + execution snapshot date; no date archive is implied.
Past fiscal years require explicit dates. Invalid, future and mismatched dates fail
before calls. Wrong returned year/date or missing business identity remains RAW but
stops INCOMPLETE. Queries always use an empty business keyword.

The version-2 budget source key includes snapshot date, fiscal year, wide/local
jurisdiction, department, business and account codes. A business-name change with
stable codes is a revision; different snapshots/jurisdictions are not overwritten.
`QWGJK_FULL_V2_SNAPSHOT` marks the new identity semantics; old rows/revisions are kept.
Reconciliation between old identities and newly replayed snapshots is not destructive.

LOFIN uses its own key and namespaced daily request counter. Its default local
safety budget is 100 attempts/day, configurable via LOFIN_VNEXT_API_DAILY_LIMIT;
this is an application safeguard, not a claim about the provider's quota.

`budget_snapshot_vnext` supplies explicit snapshot plans, receipt-backed audit,
locked-by-default bounded collection and a one-page sanitized schema probe.
Never sum multiple snapshot rows as separate annual budgets.

## Contract and analysis

- Projection fact groups carry the exact input RAW digest; stale-input races reject.
- Unresolved/remapped contracts retire old facts and relationships.
- New ambiguous final executions invalidate earlier contract assignments without
  needing the unchanged contract to be fetched again.
- Separate per-source contract projections retain multiple contracts. Singular
  award-summary contract fields are blank when ambiguous; analysis returns a list.
- Malformed joint-supplier entries remain invalid markers instead of disappearing
  and making the remaining company look like a sole supplier.
- Analysis filters retired links and checks opening/final/contract RAW digests.
  Old facts are withheld until current revisions are normalized.
- Bounded normalizers select pending/current-version work, report pending counts,
  and do not indefinitely repeat the first row.
- Manual and historical pipelines do not classify partial collection or claim
  final completion while normalization has pending work/errors.

## Verification and live limits

`python scripts/g2b_verify.py` compiles repository Python and runs every test.
Tests use disposable SQLite, empty source-key environment variables, and deny
socket connections. Retain pytest.log, pytest.xml, compile.json and the pinned
before/after source alongside the decoded applied.patch.

`python scripts/g2b_bounded_canary.py --allow-live` uses a fresh temporary DB,
not an operator-supplied production DB. G2B: yesterday KST, 6 APIs, one page of
10 rows each, one HTTP attempt per API. LOFIN: same date, one page of at most 10,
one HTTP attempt. Missing keys are BLOCKED with no source call. Error messages
are sanitized; no key, raw payload or temporary DB is uploaded. All probes keep
whole-source completeness false. A sample schema pass still needs real identity,
contract linkage, multi-page completeness and load validation before expansion.
