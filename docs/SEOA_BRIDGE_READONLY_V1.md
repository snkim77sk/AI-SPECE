# AI-SPECE · SEOA Read-only Bridge v1

Status: Draft / feature branch / production HOLD

## Purpose

Expose stored procurement/readiness context to SEOA without sharing dashboard credentials, source API keys, DB paths, RAW payloads or vendor identities.

## Authentication

Target runtime env: AI_SPACE_SEOA_BRIDGE_SECRET
SEOA runtime env: SEOA_OPS_SPACE_BRIDGE_SECRET

Use the same random >=32-character value only for this bridge pair. Do not reuse DASHBOARD_SECRET, G2B_SERVICE_KEY, LOFIN_API_KEY or any user password.

HMAC v1:
- POST only
- fixed path /api/seoa-bridge/v1/read
- timestamp within 90 seconds
- 128-bit nonce
- HMAC-SHA256 over method/path/timestamp/nonce/body hash
- bounded process-local replay cache

## Operations

- health.read: safe app/runtime projection only; no DB path or backend error text
- readiness.read: credential booleans, required table presence, dataset raw counts, checkpoint status counts, historical live collection remains locked; it never claims canary PASS
- procurement_context.read: goods or services; bounded days/category limit; aggregate dataset/category counts only

## Hard read-only database boundary

Bridge opens SQLite using URI mode=ro and PRAGMA query_only=ON.
It does not call init_db, ensure_vnext_schema, source APIs, collectors, normalizers/classifiers, scheduler or settings writers.
If required vNext tables do not exist, it returns NOT_READY instead of creating or migrating anything.

## Non-effects

- zero G2B/LOFIN API calls
- zero schema/data/settings writes
- no collection start
- no historical unlock
- no raw procurement payloads
- no vendor/business-number identities
- no deployment/main merge

Readiness status READ_ONLY_READY means only that the existing stored foundation can be read safely. It is not evidence that bounded canary or historical validation has passed.


## Monthly procurement trend read

Operation: `procurement_trend.read`

Parameters:
- kind: goods | services
- months: 3..24
- limit: 1..20 segments
- end_month: optional YYYY-MM; default current KST month

This operation reads only current-version classified stored records through the
same SQLite mode=ro connection. It returns monthly aggregate counts, never raw
payloads or organization/vendor identities.

Goods metrics:
- opportunity_count = goods bid notices
- delivery_count = shopping delivery records
- budget_count = public budget records

Services metrics:
- opportunity_count = service bid notices
- award_count = final award records
- contract_count = service contracts
- budget_count = public budget records

The result is an evidence input for SEOA's growth-signal analyzer. It is not
company revenue, profitability, or a live-source completeness claim.
