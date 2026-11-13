#!/usr/bin/env bash
# 用法：scripts/bench/run.sh up|bench|report|down
#
# 变量：NESTJS_DIR（默认 ../nestjs-template）、VU_LEVELS（默认 "10 50"）、ENDPOINTS、
#       API_CPUS（默认 2）、API_MEM（默认 1g）、WORK_DIR（默认 $TMPDIR/fastapi-bench）、OUT_DIR（默认 $WORK_DIR/results）

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BENCH_DIR="$ROOT/scripts/bench"
NESTJS_DIR="${NESTJS_DIR:-$ROOT/../nestjs-template}"
NESTJS_COMMIT="$(sed -n 's/^commit=//p' "$ROOT/openapi/SOURCE")"
NESTJS_IMAGE="${NESTJS_IMAGE:-fastapi-temp/nestjs-ref:${NESTJS_COMMIT:0:12}}"
FASTAPI_IMAGE="${FASTAPI_IMAGE:-fastapi-temp:bench}"
K6_IMAGE="${K6_IMAGE:-grafana/k6:1.3.0}"
FASTAPI_PROJECT=bench-fastapi
NESTJS_PROJECT=bench-nestjs
VU_LEVELS="${VU_LEVELS:-10 50}"
ENDPOINTS="${ENDPOINTS:-login refresh me users}"
API_CPUS="${API_CPUS:-2}"
API_MEM="${API_MEM:-1g}"
WORK_DIR="${WORK_DIR:-${TMPDIR:-/tmp}/fastapi-bench}"
OUT_DIR="${OUT_DIR:-$WORK_DIR/results}"

mkdir -p "$WORK_DIR"

write_overrides() {
  cat > "$WORK_DIR/fastapi.override.yaml" <<YAML
x-image: &image
  image: $FASTAPI_IMAGE
  pull_policy: never

services:
  pidns: *image
  migrate: *image
  worker: *image
  scheduler: *image
  api:
    <<: *image
    cpus: $API_CPUS
    mem_limit: $API_MEM
YAML
  cat > "$WORK_DIR/nestjs.override.yaml" <<YAML
services:
  api:
    environment:
      NODE_ENV: production
      LOG_LEVEL: info
      LOG_PRETTY_PRINT: 'false'
    ports: !override
      - '127.0.0.1::3000'
    cpus: $API_CPUS
    mem_limit: $API_MEM
YAML
  git -C "$NESTJS_DIR" show "$NESTJS_COMMIT:env-example-relational" \
    | sed -e 's/^THROTTLE_ENABLED=.*/THROTTLE_ENABLED=false/' \
      -e 's/^\([A-Z_]*_SECRET\)=\(secret.*\)/\1=\2_0123456789abcdefghijklmnop/' \
      > "$WORK_DIR/nestjs.env"
}

fastapi_compose() {
  CI_THROTTLE_ENABLED=false docker compose -p "$FASTAPI_PROJECT" \
    -f "$ROOT/docker/docker-compose.base.yaml" \
    -f "$ROOT/docker/docker-compose.postgres.yaml" \
    -f "$ROOT/docker/docker-compose.redis.yaml" \
    -f "$ROOT/docker/docker-compose.ci.yaml" \
    -f "$WORK_DIR/fastapi.override.yaml" "$@"
}

nestjs_compose() {
  NESTJS_IMAGE="$NESTJS_IMAGE" NESTJS_ENV_FILE="$WORK_DIR/nestjs.env" docker compose -p "$NESTJS_PROJECT" \
    -f "$ROOT/scripts/nestjs/nestjs.compose.yaml" \
    -f "$WORK_DIR/nestjs.override.yaml" "$@"
}

wait_ready() {
  local project="$1"
  for _ in $(seq 1 90); do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$project-api-1" 2>/dev/null)" != true ]]; then
      echo "$project: api 未在运行" >&2
      return 1
    fi
    if docker run --rm --network "${project}_default" --entrypoint wget "$K6_IMAGE" -q -O /dev/null http://api:3000/ 2>/dev/null; then
      return 0
    fi
    sleep 2
  done
  echo "$project: api 未在 180s 内就绪" >&2
  return 1
}

up() {
  write_overrides
  fastapi_compose up --detach --wait --wait-timeout 300
  nestjs_compose up --detach --wait postgres redis
  nestjs_compose up --detach api
  wait_ready "$FASTAPI_PROJECT"
  wait_ready "$NESTJS_PROJECT"
}

down() {
  write_overrides
  fastapi_compose down --volumes --remove-orphans
  nestjs_compose down --volumes --remove-orphans
}

run_k6() {
  local backend="$1" endpoint="$2" vus="$3"
  local project="$FASTAPI_PROJECT"
  [[ "$backend" == nestjs ]] && project="$NESTJS_PROJECT"
  docker run --rm --network "${project}_default" \
    -v "$BENCH_DIR:/scripts:ro" -v "$OUT_DIR:/out" \
    -e BASE_URL=http://api:3000 -e ENDPOINT="$endpoint" -e VUS="$vus" \
    -e SUMMARY_FILE="/out/$backend-$endpoint-$vus.json" \
    "$K6_IMAGE" run --quiet --no-color /scripts/auth.js
}

bench() {
  mkdir -p "$OUT_DIR"
  local round=0
  for vus in $VU_LEVELS; do
    for endpoint in $ENDPOINTS; do
      local order=(fastapi nestjs)
      ((round % 2)) && order=(nestjs fastapi)
      round=$((round + 1))
      for backend in "${order[@]}"; do
        echo "== $backend $endpoint vus=$vus"
        run_k6 "$backend" "$endpoint" "$vus"
      done
    done
  done
  report
}

report() {
  printf '| 接口 | VU | 后端 | 请求数 | RPS | p50 (ms) | p95 (ms) | p99 (ms) | 错误率 |\n'
  printf '|---|---|---|---|---|---|---|---|---|\n'
  for endpoint in $ENDPOINTS; do
    for vus in $VU_LEVELS; do
      for backend in fastapi nestjs; do
        jq -r --arg b "$backend" '
          def r1: . * 10 | round / 10;
          "| \(.endpoint) | \(.vus) | \($b) | \(.requests) | \(.rps | r1) | \(.p50 | r1) | \(.p95 | r1) | \(.p99 | r1) | \(.errorRate * 10000 | round / 100)% |"
        ' "$OUT_DIR/$backend-$endpoint-$vus.json"
      done
    done
  done
}

case "${1:-}" in
  up) up ;;
  bench) bench ;;
  report) report ;;
  down) down ;;
  *)
    echo "用法：$0 up|bench|report|down" >&2
    exit 2
    ;;
esac
