# AI-SPECE 3.2 Hybrid Operation

## Architecture

- Office PC: official source collection -> RAW -> immutable revisions -> post-classification -> analysis.
- Cafe24: compact result snapshot only -> dashboard/search/vendor/service/budget pages.
- Cafe24 source collection is disabled by default (`RESULT_SERVER`).
- The local script explicitly runs as `LOCAL_COLLECTOR`.

## First setup

1. Deploy 3.2.0 to Cafe24.
2. Log in to Cafe24 > Settings > Local PC -> Cafe24 result sync.
3. Click `동기화 토큰 새로 발급` and copy the one-time token.
4. On the office PC clone this repository and install requirements:

   `python -m pip install -r requirements.txt`

5. Run:

   `python scripts/local_collector.py --g2b-key YOUR_G2B_KEY --server https://YOUR-SERVER --token YOUR_SYNC_TOKEN --interval-minutes 120`

The default local database is `local_data/g2b-local.sqlite3`.
The compressed local result backup is `local_data/result-snapshot.json.gz`.

## Cafe24 disk cleanup

Do not delete the old Cafe24 RAW before the first local snapshot is visible in Settings.

After the first successful snapshot:
- Settings > Local PC -> Cafe24 result sync
- type `RESULT_ONLY`
- click `기존 RAW 삭제 후 디스크 회수`

This operation preserves:
- administrator/users/sessions
- app settings
- source credentials and result sync token
- compact result snapshot database

It removes source-heavy vNext RAW/revision/checkpoint/projection tables from the Cafe24 control database and attempts `VACUUM`.

## Bounded manual collection

For a safe manual continuation on an existing local database, set an exact inclusive
date range. Verified COMPLETE dates inside the range are skipped and existing RAW is
not deleted or reset.

Example for 2026-09-02 through 2026-09-04:

`python scripts/local_collector.py --db D:\\G2B\\data\\g2b-local.sqlite3 --start-date 2026-09-02 --end-date 2026-09-04 --max-days 3 --output D:\\G2B\\snapshot\\result-snapshot.json.gz`

The G2B service key should be supplied through the local encrypted-key launcher or
the `G2B_SERVICE_KEY` environment variable rather than written into a persistent
command history.

The CLI rejects an end date later than Korea D-1. Omitting `--end-date` keeps the
forward collector behavior and advances only through the latest completed source day.

## One-click full catch-up on Windows

After copying the 3.2.3 program into `D:\G2B\program`, double-click:

`D:\G2B\program\RUN_FULL_COLLECTION.cmd`

The launcher:
- reads `D:\G2B\g2b-service-key.dpapi` for the current Windows user without printing the key;
- uses the existing `D:\G2B\data\g2b-local.sqlite3` without deleting or resetting it;
- starts from 2026-09-01 but skips verified COMPLETE dates;
- leaves `--end-date` unset so the local collector recalculates Korea D-1;
- writes the refreshed result snapshot to `D:\G2B\snapshot\result-snapshot.json.gz`;
- writes a timestamped operational log under `D:\G2B\logs`;
- remains local-only because no Cafe24 server/token is passed.

If a run stops, run the same CMD again. Stored checkpoints are reused and completed
days remain skipped.

## Automatic local collection

Automatic mode is local-PC only. Keep `--end-date` omitted so every cycle recalculates
Korea D-1, skips verified COMPLETE dates, and resumes the oldest unfinished date.

Example:

`python scripts/local_collector.py --db D:\\G2B\\data\\g2b-local.sqlite3 --start-date 2026-09-01 --output D:\\G2B\\snapshot\\result-snapshot.json.gz --interval-minutes 120`

Behavior:
- only one collector process may own the same local DB at a time;
- a second process returns `SKIPPED_ALREADY_RUNNING` without touching RAW;
- one failed automatic cycle is reported with a safe error type/code and the next
  interval retries from stored checkpoints;
- the process lock is released automatically by the operating system after a crash;
- `--once` forces one cycle even when `--interval-minutes` is present;
- interval 0 remains the original one-shot behavior and still exits on an error.

This long-running mode is optional. A future Windows Task Scheduler job may instead
launch one-shot runs periodically; the same DB process lock prevents overlap.

## Local-only use

Omit `--server` and `--token` to collect/analyze locally and only write the compressed snapshot file.

## Security

Prefer environment variables instead of putting secrets in a persistent shell history:
- `G2B_SERVICE_KEY`
- `G2B_RESULT_SERVER_URL`
- `G2B_RESULT_SYNC_TOKEN`
- `G2B_LOCAL_DB_PATH`

The result upload uses HTTPS bearer-token authentication. No source API key is included in the result snapshot.
