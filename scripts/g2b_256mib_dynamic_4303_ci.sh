#!/usr/bin/env bash
# DRAFT: 256MiB web+shopping+budget using ONLY disposable CI PostgreSQL.
set -euo pipefail
test "$G2B_DATABASE_URL" = "postgresql://postgres:postgres@127.0.0.1:5432/g2b_vnext_test"
test -z "$G2B_SERVICE_KEY"
test -z "$LOFIN_API_KEY"
test -z "$EDUINFO_API_KEY"
name=g2b4303-dynamic-ci
cleanup() {
  docker exec "$name" sh -c 'tail -n 60 /tmp/g2b-4303-web.log 2>/dev/null || true' 2>/dev/null || true
  docker rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker run --rm -d --name "$name" --memory=256m --memory-swap=256m --pids-limit=96   --network host --security-opt=no-new-privileges   --tmpfs /tmp:rw,nosuid,nodev,size=24m   --mount "type=bind,source=$(pwd),target=/app,readonly" --workdir /app   -e G2B_DATABASE_URL="$G2B_DATABASE_URL" -e G2B_RUNTIME_ROLE=UNIFIED   -e G2B_TEST_MODE=0 -e G2B_AUTO_SYNC_DISABLE=1 -e G2B_V41_FRESH_START=0   -e G2B_DESTRUCTIVE_RESET_CONFIRM=0 -e G2B_POST_BOOT_MAINTENANCE_ENABLE=0   -e G2B_256MIB_DYNAMIC_SOURCE_BUDGET=1   -e G2B_APP_SCHEMA=g2b_app_4292_ci -e G2B_BUDGET_SCHEMA=g2b_budget_4292_ci   -e G2B_SERVICE_KEY='' -e LOFIN_API_KEY='' -e EDUINFO_API_KEY=''   -e G2B_MEMORY_SOFT_LIMIT_MB=160 -e G2B_ISOLATED_WORKER_SOFT_LIMIT_MB=112   -e PORT=8034 g2b-4-1-303-ci sleep 420 >/dev/null
test "$(docker exec "$name" cat /sys/fs/cgroup/memory.max)" = "268435456"
docker exec -d "$name" sh -c 'exec python -B run.py >/tmp/g2b-4303-web.log 2>&1'
live=0
for i in $(seq 1 160); do
  if curl -fsS --max-time 2 http://127.0.0.1:8034/live >/dev/null 2>&1; then
    live=1; break
  fi
  sleep .25
done
test "$live" = 1
ready=0
for i in $(seq 1 200); do
  if test "$(curl -sS --max-time 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:8034/ready 2>/dev/null || true)" = 200; then
    ready=1; break
  fi
  sleep .25
done
test "$ready" = 1
curl -fsS --max-time 3 http://127.0.0.1:8034/health >"$RUNNER_TEMP/g2b-4303-health.json"
python - "$RUNNER_TEMP/g2b-4303-health.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding="utf-8"))
assert d["memory_cgroup_source"] == "cgroup-v2", d
assert d["memory_cgroup_limit_mib"] == 256, d
assert d["memory_worker_dynamic_source_enabled"] is True, d
assert d["memory_worker_dynamic_ceiling_bytes"] == 220_000_000, d
assert d["memory_worker_forecast_basis"] == "CGROUP_RAW_DYNAMIC_220000000_BYTES", d
assert d["memory_worker_expected_mib"] == 32, d
assert d["memory_worker_reserve_mib"] == 0, d
print("G2B_4303_REAL_PARENT_CGROUP",{
    "raw":d["memory_cgroup_current_mib"],
    "effective":d["memory_cgroup_effective_mib"],
    "admission":d["memory_worker_prelaunch_capacity_reason"],
})
PY
before="$(docker exec "$name" awk '$1=="oom_kill"{print $2}' /sys/fs/cgroup/memory.events)"
for source in shopping budget; do
  log="$RUNNER_TEMP/g2b-4303-$source.log"
  docker exec -e G2B_CI_SYNTHETIC_PAGES=1 "$name"     python -B scripts/g2b_256mib_synthetic_page_ci.py "$source" >"$log" 2>&1 &
  pid=$!
  for i in $(seq 1 95); do
    curl -fsS --max-time 3 http://127.0.0.1:8034/live >/dev/null
    sleep .15
  done
  set +e
  wait "$pid"
  code=$?
  set -e
  tail -n 40 "$log"
  test "$code" = 0
  grep -q "G2B_4293_SYNTHETIC_PAGE_COMPLETE $source rows " "$log"
  test "$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8034/ready)" = 200
done
after="$(docker exec "$name" awk '$1=="oom_kill"{print $2}' /sys/fs/cgroup/memory.events)"
test "$after" = "$before"
peak="$(docker exec "$name" cat /sys/fs/cgroup/memory.peak)"
if (( peak > 220000000 )); then
  echo "G2B_4303_RAW_PEAK_EXCEEDED_DECIMAL_TARGET $peak" >&2
  exit 1
fi
echo "G2B_4303_REAL_CGROUP_BOTH_SYNTHETIC_SOURCES_OK peak_bytes=$peak oom_kill_delta=0"
echo "G2B_4303_REAL_SOURCE_API_CALLS=0"
