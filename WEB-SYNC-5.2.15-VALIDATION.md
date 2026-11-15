# GPT web synchronization 5.2.15

The deployed Site rejected the verified, empty 2026-10-31 G2B page because its checkpoint used `source_total=-1` (unknown). The response was HTTP 422 / `SYNC_INVALID_CHECKPOINT`. The Windows uploader then cleared the remaining delivery queue, blocking later pages and incorrectly reporting zero pending pages.

The receiver now accepts exactly `-1` for an unknown source total while preserving all other checkpoint validation. An empty verified page receives an idempotent receipt without deleting previously stored data. The Windows uploader defers rejected pages for 15, 30, then 60 minutes, continues other pages, and counts all unsynchronized pages in its pending total. It records only a safe HTTP status and public error code. Receipt lookup before upload and readback after upload remain mandatory.

## Validation on 2026-11-15

- Replayed the exact empty-page payload: the old receiver rejected it; the updated receiver and production Site accepted it.
- Site tests: all 702 distinct tests passed. The first full run had one XLSX environment failure; all seven tests in that file passed with the existing virtual environment on PATH. Production build passed.
- Windows sync/status tests: 29 passed. Coverage includes rejected-page isolation, persistent retry delay, lost-response receipt recovery, pending counts, and diagnostic redaction.
- Production transfer at 23:18 KST: 8 pages attempted, stored and independently read back; 6,993 source rows and 184 matching query rows; zero source-provider API calls; no errors; 300 pages remained pending. Duration: 18.435 seconds.
- The formerly blocked empty page retained `source_total=-1` and `COMPLETE` status. SHA-256 values of both source databases used by this transfer were unchanged.
- The deployed Site is app 5.2.15 / Site version 51, built from native source commit `9ea209c590c183986a92f560ddada7623f8ed7ad`.
- A new 22-file runtime was installed at `D:\G2B\gpt-site-sync-v5.2.15` with a SHA-256 manifest. Only the action of the idle `G2B-GPT-Site-Sync` task changed; its 15-minute and login triggers, settings and principal were preserved. The old task XML and runtime remain available for rollback.

The source collector remains 5.2.14. Collection and web synchronization are separate: the above figures prove web receipt/storage verification, not nationwide or year-to-date collection completion. The existing scheduler continues draining pending pages in bounded batches. The release PR records the next regular scheduled run and GitHub CI results before main merge.

No credentials, source databases, RAW data or checkpoints are included in this release. Site access remains owner-private. Cafe24 and NO1 are unchanged.
