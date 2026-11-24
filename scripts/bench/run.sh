#!/usr/bin/env bash
# 用法：scripts/bench/run.sh up|bench|report|down|all
#
# 变量：VU_LEVELS（默认 "10 50 100"）、ENDPOINTS（默认 "login me users notes"）、API_CPUS（默认 2）、
#       API_MEM（默认 1g）、BENCH_IMAGE（默认 fastapi-temp:bench）、WORK_DIR（默认 $TMPDIR/fastapi-bench）、
#       OUT_DIR（默认 $WORK_DIR/results）

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BENCH_DIR="$ROOT/scripts/bench"
BENCH_IMAGE="${BENCH_IMAGE:-fastapi-temp:bench}"
K6_IMAGE="${K6_IMAGE:-grafana/k6:1.3.0}"
PROJECT=bench-fastapi
VU_LEVELS="${VU_LEVELS:-10 50 100}"
ENDPOINTS="${ENDPOINTS:-login me users notes}"
API_CPUS="${API_CPUS:-2}"
API_MEM="${API_MEM:-1g}"
WORK_DIR="${WORK_DIR:-${TMPDIR:-/tmp}/fastapi-bench}"
OUT_DIR="${OUT_DIR:-$WORK_DIR/results}"

mkdir -p "$WORK_DIR"

write_override() {
  cat > "$WORK_DIR/bench.override.yaml" <<YAML
x-image: &image
  image: $BENCH_IMAGE
  pull_policy: never

services:
  migrate: *image
  worker: *image
  scheduler: *image
  api:
    <<: *image
    environment:
      RATE_LIMIT_DEFAULT: 1/ms,burst=100000000
    cpus: $API_CPUS
    mem_limit: $API_MEM
YAML
}

stack() {
  docker compose -p "$PROJECT" \
    -f "$ROOT/docker/docker-compose.base.yaml" \
    -f "$ROOT/docker/docker-compose.postgres.yaml" \
    -f "$ROOT/docker/docker-compose.redis.yaml" \
    -f "$ROOT/docker/docker-compose.ci.yaml" \
    -f "$WORK_DIR/bench.override.yaml" "$@"
}

up() {
  docker build --target production -t "$BENCH_IMAGE" "$ROOT"
  write_override
  stack up --detach --wait --wait-timeout 300
}

down() {
  write_override
  stack down --volumes --remove-orphans
}

run_k6() {
  local endpoint="$1" vus="$2"
  docker run --rm --network "${PROJECT}_default" \
    -v "$BENCH_DIR:/scripts:ro" -v "$OUT_DIR:/out" \
    -e BASE_URL=http://api:3000 -e ENDPOINT="$endpoint" -e VUS="$vus" \
    -e SUMMARY_FILE="/out/$endpoint-$vus.json" \
    "$K6_IMAGE" run --quiet --no-color /scripts/api.js
}

bench() {
  mkdir -p "$OUT_DIR"
  for vus in $VU_LEVELS; do
    for endpoint in $ENDPOINTS; do
      echo "== $endpoint vus=$vus"
      run_k6 "$endpoint" "$vus"
    done
  done
  report
}

report() {
  printf '| 接口 | VU | 请求数 | RPS | p50 (ms) | p95 (ms) | p99 (ms) | 错误率 |\n'
  printf '|---|---|---|---|---|---|---|---|\n'
  for endpoint in $ENDPOINTS; do
    for vus in $VU_LEVELS; do
      jq -r '
        def r1: . * 10 | round / 10;
        "| \(.endpoint) | \(.vus) | \(.requests) | \(.rps | r1) | \(.p50 | r1) | \(.p95 | r1) | \(.p99 | r1) | \(.errorRate * 10000 | round / 100)% |"
      ' "$OUT_DIR/$endpoint-$vus.json"
    done
  done
}

case "${1:-}" in
  up) up ;;
  bench) bench ;;
  report) report ;;
  down) down ;;
  all)
    trap down EXIT
    up
    bench
    ;;
  *)
    echo "用法：$0 up|bench|report|down|all" >&2
    exit 2
    ;;
esac
