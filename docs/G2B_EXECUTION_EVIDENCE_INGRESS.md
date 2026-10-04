# G2B execution evidence one-way ingress contract

## Purpose

This contract carries only realized execution evidence into G2B. It does not share
the NO1 database, schema, ORM models, prediction data, or source credentials.

Schema version:

`g2b-execution-evidence-import-v1`

Accepted source systems:

- `NO1_EXPORT`
- `OFFICIAL_G2B_EXPORT`

Maximum rows per document: 5,000.

## NO1 field mapping

A future NO1 exporter may read its own database and write a standalone JSON document.
G2B receives only that document.

| Import field | Preferred NO1 source | Rule |
| --- | --- | --- |
| business_type | Tender.kind | service or construction only |
| notice_no | Tender.notice_no | required |
| notice_order | Tender.notice_order | required; never guess missing as zero |
| classification | Tender.classification | required |
| rebid_no | Tender.rebid_no | required |
| award_date | official final-award/opening source date | required; do not substitute application observation time when an official date exists |
| organization_code | demand Institution.id/code | demand institution preferred |
| organization_name | demand Institution.name | demand institution preferred |
| title | Tender.title | required |
| winner_id | BidResult.winner_id | optional if name or amount exists |
| winner_name | current official winner/company source | optional |
| winning_amount | BidResult.winning_amount | optional if winner identity exists |
| award_rate | official award rate if available | optional |
| source_row_digest | exporter SHA-256 of canonical compact row | optional; G2B recomputes and verifies |

At least one of organization_code / organization_name is required.
At least one of winner_id / winner_name / winning_amount is required.

## Explicitly excluded

The exact allowlist rejects extra fields. In particular, do not export:

- participant rows or participant_count
- scheduled price
- preliminary/reserve prices
- recommendations or candidate scores
- model snapshots or prediction inputs
- raw source payload/public_snapshot
- API credentials or request URLs

Those belong to NO1 or source provenance and are not needed to answer the G2B question:
"did this budget project later execute as a service/electrical/lighting award?"

## Matching and persistence

G2B validates the document before any write. It then reuses the conservative budget
matcher:

- same fiscal year
- exact institution/organization or safe administrative lineage
- at least one distinctive project/facility/locality identity
- generic LED/lighting/electrical words alone never match
- one official execution may persist against only one budget project
- tied budget assignments are rejected

Only matched compact evidence persists. The imported document/raw row is not stored.
For auditability the evidence row stores only `ingress_source` and
`ingress_row_digest`.

## Official API fallback

Official G2B award/contract APIs remain outside the G2B operational source allowlist.
If a NO1 export lacks an execution, a separate future bounded process may query the
official award service or contract-process service and emit the same
`OFFICIAL_G2B_EXPORT` document. That process must not widen ordinary G2B collection.

Preferred order:

1. reuse already stored NO1 outcome facts through one-way export;
2. identify gaps by notice/execution identity;
3. query only those gaps from the official service;
4. import the same compact contract;
5. never bulk-copy NO1 prediction/raw tables into G2B.
