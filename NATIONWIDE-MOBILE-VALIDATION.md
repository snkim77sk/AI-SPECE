# 전국 예산 조회·모바일 검증 — 5.2.10

2026-11-15 KST. GPT Site native source `2a7b5304f4b53887feadb4ad2cd39310487c56c8`, Sites version 48. Existing owner-only access retained. Cafe24 remains retired.

## Cause and changes

The budget selector combined stored QWGJK regions (서울, 전남광주) with empty legacy 서울특별시/경기도 base choices. QWGJK collection is incomplete. Separately, the current AIDFA structure data covers 17 source regions, 11,480 records. These source populations are now shown separately. Missing current-source coverage is explicitly labelled, and 충남/충북 shortcuts are available.

Stored short region names (충남, 충북, 서울, 인천, etc.) now match corresponding full-name read filters. Provider names, organization codes, original rows and checkpoints are preserved. This is a query alias, not a merge of organizations or evidence of administrative identity.

Date-bearing queries open at the seven inclusive days ending yesterday in Korea, with recent-first ordering. On 2026-11-15 this is 2026-11-08 through 2026-11-14. Explicit/bookmarked dates and deliberately empty all-period dates persist. The annual AIDFA view keeps the full fiscal year; it has no historical execution-date filter. Current list, summary and export share predicates; history retains its own observation date semantics.

Mobile budget rows use readable cards, 16px inputs, at least 44px controls, wrapping amounts and a results jump. Extra help is expandable and prior functions remain reachable. Full totals are calculated on request, keeping ordinary list reads from issuing repeated heavy aggregates. Compact SQL amount extraction avoids repeatedly parsing full raw payloads without materializing all projects in the Worker.

## Checks

- Sites: 697 tests passed, TypeScript check passed, production build/package passed.
- Viewer: 78 tests passed.
- Server CI groups, separate Python processes: G2B 54, LOFIN 24, sync/status 26, source isolation 3, recovery 30; total 137 passed. A first combined invocation collided in module imports; rerun in the repository CI's separate-process configuration passed.
- Local responsive browser: 360×800 and 390×844. No document horizontal overflow; 100 returned rows visible as cards. Seoul short-name/full-name filter returned actual saved rows.
- 90,000-row local SQL performance probe: preflight 2.129 s, exact sums 2.465 s; large integer total matched BigInt independently. This is a local timing, not a production latency guarantee.
- Deployed version and live readback evidence: private Site v48, application 5.2.10. Live coverage reports AIDFA 11,480 / 17 source regions, 충남 830 / 충북 600. All 830 Chungnam rows (9 pages) and 600 Chungbuk rows (6 pages) were read back with zero duplicate keys. QWGJK returned 100 dated Seoul rows; the full filtered summary counted 32,721 projects with complete valid money. This readback is recorded in the task evidence JSON.

## Operational scope

This change made 0 provider API calls and wrote 0 source records. Government API collectors and their 15-minute schedules continue independently. No collector binary, API quota, original database, raw record or checkpoint was modified by this release. Read-only validation and ordinary web synchronization are distinct from source collection.

G2B and QWGJK nationwide collection are still incomplete; a region with no reflected detailed rows must not be interpreted as no budget. Snapshot synchronization can lag server storage and retries pending transfers on its existing schedule. The UI shows its last reflection time and revision.
