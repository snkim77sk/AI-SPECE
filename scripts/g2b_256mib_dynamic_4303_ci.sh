#!/usr/bin/env bash
# CI-only integrated real parent/cgroup canary. Never a deployment entry point.
set -euo pipefail
test "${G2B_DATABASE_URL:-}" = "postgresql://postgres:postgres@127.0.0.1:5432/g2b_vnext_test"
test -z "${G2B_SERVICE_KEY:-}"
test -z "${LOFIN_API_KEY:-}"
test -z "${EDUINFO_API_KEY:-}"
command -v docker >/dev/null || { echo 'DOCKER_UNAVAILABLE_REAL_CGROUP_NOT_RUN' >&2; exit 2; }
python -B scripts/g2b_4303_parent_cgroup_ci.py "$@"
