#!/usr/bin/env bash
# Source-free, disposable PG canary. No real Cafe24, procurement APIs or keys.
set -euo pipefail
if [[ "$G2B_DATABASE_URL" != "postgresql://postgres:postgres@127.0.0.1:5432/g2b_vnext_test" ]]; then
  echo "G2B_4292_REJECT_NON_CI_DATABASE" >&2; exit 2
fi
if [[ -n "$G2B_SERVICE_KEY" || -n "$LOFIN_API_KEY" || -n "$EDUINFO_API_KEY" ]]; then
  echo "G2B_4292_REJECT_SOURCE_CREDENTIALS" >&2; exit 2
fi
name="g2b4292-cgroup-ci"
image="g2b-4-1-292-ci"
cleanup() {
  docker exec "$name" sh -c 'cat /tmp/g2b-256-web.log 2>/dev/null || true' 2>/dev/null || true
  docker rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker run --rm -d \
  --name "$name" --memory=256m --memory-swap=256m --pids-limit=96 \
  --network host --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=24m \
  --mount "type=bind,source=$(pwd),target=/app,readonly" \
  --workdir /app \
  -e G2B_DATABASE_URL="$G2B_DATABASE_URL" \
  -e G2B_RUNTIME_ROLE=UNIFIED -e G2B_TEST_MODE=0 \
  -e G2B_AUTO_SYNC_DISABLE=1 -e G2B_POST_BOOT_MAINTENANCE_ENABLE=0 \
  -e G2B_MATCH_ROLLOVER_AUTO_ENABLE=0 -e G2B_V41_FRESH_START=0 \
  -e G2B_DESTRUCTIVE_RESET_CONFIRM=0 \
  -e G2B_SERVICE_KEY='' -e LOFIN_API_KEY='' -e EDUINFO_API_KEY='' \
  -e G2B_APP_SCHEMA=g2b_app_4292_ci -e G2B_BUDGET_SCHEMA=g2b_budget_4292_ci \
  -e G2B_MEMORY_SOFT_LIMIT_MB=160 -e G2B_ISOLATED_WORKER_SOFT_LIMIT_MB=112 \
  -e PORT=8033 \
  "$image" sleep 240 >/dev/null

limit="$(docker exec "$name" cat /sys/fs/cgroup/memory.max)"
if [[ "$limit" != "268435456" ]]; then
  echo "G2B_4292_WRONG_REAL_CGROUP_LIMIT: $limit" >&2; exit 1
fi
docker exec -d "$name" sh -c 'exec python -B run.py >/tmp/g2b-256-web.log 2>&1'
live=0
for _ in $(seq 1 120); do
  if curl -fsS --max-time 2 http://127.0.0.1:8033/live >/dev/null 2>&1; then
    live=1; break
  fi
  sleep .25
done
if [[ "$live" != 1 ]]; then
  echo "G2B_4292_WEB_NOT_LIVE" >&2; exit 1
fi
ready=0
for _ in $(seq 1 180); do
  status="$(curl -sS --max-time 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:8033/ready 2>/dev/null || true)"
  if [[ "$status" = 200 ]]; then ready=1; break; fi
  sleep .25
done
if [[ "$ready" != 1 ]]; then
  echo "G2B_4292_PG_WEB_NOT_READY" >&2; exit 1
fi
before_kills="$(docker exec "$name" awk '$1=="oom_kill"{print $2}' /sys/fs/cgroup/memory.events)"
for source in shopping budget; do
  log_path="$RUNNER_TEMP/g2b-4292-$source.log"
  docker exec "$name" python -B -m g2b_heavy_worker "$source" >"$log_path" 2>&1 &
  worker_pid=$!
  for _ in $(seq 1 40); do
    if ! curl -fsS --max-time 3 http://127.0.0.1:8033/live >/dev/null; then
      echo "G2B_4292_WEB_DIED_DURING_$source" >&2; exit 1
    fi
    sleep .25
  done
  set +e
  wait "$worker_pid"
  rc=$?
  set -e
  cat "$log_path"
  if [[ "$rc" != 72 ]]; then
    echo "G2B_4292_SOURCE_UNEXPECTED_EXIT $source code=$rc" >&2; exit 1
  fi
  grep -q "G2B_HEAVY_WORKER_SOURCE_STATE $source WAITING_KEYS 72" "$log_path"
  python - "$source" "$log_path" <<'PY'
import json
from pathlib import Path
import sys
name, path = sys.argv[1:]
prefix = f"G2B_SOURCE_WORKER_MEMORY {name} source_state "
lines = [x for x in Path(path).read_text().splitlines() if x.startswith(prefix)]
assert len(lines) == 1, (name, len(lines))
result = json.loads(lines[0][len(prefix):])
assert result["sampled_cgroup_limit_mib"] == 256.0, result
assert result["rss_peak_mib"] > 0, result
assert result["samples"] >= 2, result
assert result["sampled_peak_cgroup_effective_mib"] > 0, result
print("G2B_4292_REAL_256MIB_SOURCE_FREE_PROFILE", name, result)
PY
  test "$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8033/ready)" = 200
done
after_kills="$(docker exec "$name" awk '$1=="oom_kill"{print $2}' /sys/fs/cgroup/memory.events)"
if [[ "$after_kills" != "$before_kills" ]]; then
  echo "G2B_4292_CGROUP_OOM_KILL_INCREASED: $before_kills -> $after_kills" >&2; exit 1
fi
peak_bytes="$(docker exec "$name" cat /sys/fs/cgroup/memory.peak)"
echo "G2B_4292_256MIB_SOURCE_FREE_CGROUP_OK peak_bytes=$peak_bytes oom_kill_delta=0"
echo "G2B_4292_EXTERNAL_SOURCE_API_CALLS=0"
