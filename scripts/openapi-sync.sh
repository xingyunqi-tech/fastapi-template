#!/usr/bin/env bash
# 在 nestjs-template 固定提交上导出 openapi/ 基线、validation.json 与校验用例期望结果。
#
# 变量：
#   NESTJS_DIR         nestjs-template 本地仓库（默认 ../nestjs-template）
#   NESTJS_REF         导出所用提交（默认取 openapi/SOURCE 的 commit）
#   NESTJS_BRANCH      记入 SOURCE 的分支名（默认 release/exam-prep）
#   NESTJS_SCRIPT_REF  scripts/export-validation.ts 所在提交（默认取 openapi/SOURCE 的 validation_script，否则 NESTJS_BRANCH）
#   NESTJS_PORT        导出期间 nestjs 映射到宿主的端口（默认 3001）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/openapi"
NESTJS_DIR="${NESTJS_DIR:-$ROOT/../nestjs-template}"
NESTJS_BRANCH="${NESTJS_BRANCH:-release/exam-prep}"
export NESTJS_PORT="${NESTJS_PORT:-3001}"

if [[ -z "${NESTJS_REF:-}" && -f "$OUT/SOURCE" ]]; then
  NESTJS_REF="$(sed -n 's/^commit=//p' "$OUT/SOURCE")"
fi
NESTJS_REF="$(git -C "$NESTJS_DIR" rev-parse --verify "${NESTJS_REF:?NESTJS_REF is required}^{commit}")"
if [[ -z "${NESTJS_SCRIPT_REF:-}" && -f "$OUT/SOURCE" ]]; then
  NESTJS_SCRIPT_REF="$(sed -n 's/^validation_script=//p' "$OUT/SOURCE")"
fi
NESTJS_SCRIPT_REF="$(git -C "$NESTJS_DIR" rev-parse --verify "${NESTJS_SCRIPT_REF:-$NESTJS_BRANCH}^{commit}")"

WORKTREE="$(mktemp -d)"
export NESTJS_IMAGE="fastapi-temp/nestjs-ref:${NESTJS_REF:0:12}"
BUILDER_IMAGE="fastapi-temp/nestjs-ref:${NESTJS_REF:0:12}-builder"
CASES_DIR="$ROOT/tests/contract/fixtures"
export NESTJS_ENV_FILE="$WORKTREE/env-example-relational"
STAGE="$(mktemp -d)"
TMP_BASELINE="$STAGE/nestjs-baseline.json"
COMPOSE=(docker compose -p nestjs-openapi-sync -f "$ROOT/docker/nestjs.compose.yaml")

cleanup() {
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  git -C "$NESTJS_DIR" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
  rm -rf "$WORKTREE" "$STAGE"
}
trap cleanup EXIT

echo "==> nestjs-template ${NESTJS_REF}"
git -C "$NESTJS_DIR" worktree add --detach "$WORKTREE" "$NESTJS_REF" >/dev/null

git -C "$NESTJS_DIR" show "${NESTJS_SCRIPT_REF}:scripts/export-validation.ts" >"$WORKTREE/scripts/export-validation.ts"

echo "==> 构建镜像 ${NESTJS_IMAGE}"
docker build --target builder -t "$BUILDER_IMAGE" "$WORKTREE"
docker build --target production -t "$NESTJS_IMAGE" "$WORKTREE"

run_ts() {
  local script="$1"
  shift
  docker run --rm -v "$script:/app/scripts/$(basename "$script"):ro" "$@" "$BUILDER_IMAGE" \
    pnpm exec ts-node -P tsconfig.app.json -O '{"rootDir":"."}' "scripts/$(basename "$script")" /tmp/cases.json
}

echo "==> 导出 validation.json"
run_ts "$WORKTREE/scripts/export-validation.ts" >"$STAGE/validation.json"
python3 -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$STAGE/validation.json"

echo "==> 生成校验用例期望结果"
mkdir -p "$CASES_DIR"
uv run --frozen python "$ROOT/scripts/gen_validation_cases.py" "$STAGE/validation.json" >"$WORKTREE/cases.json"
run_ts "$ROOT/scripts/nestjs/validation-cases.ts" -v "$WORKTREE/cases.json:/tmp/cases.json:ro" >"$WORKTREE/expected.json"
uv run --frozen python "$ROOT/scripts/gen_validation_cases.py" "$STAGE/validation.json" \
  --merge "$WORKTREE/cases.json" "$WORKTREE/expected.json" >"$STAGE/validation-cases.json"

echo "==> 启动 nestjs（FILE_DRIVER=local）"
"${COMPOSE[@]}" up -d --wait postgres redis
"${COMPOSE[@]}" up -d api

url="http://localhost:${NESTJS_PORT}/docs-json"
fetched=false
for _ in $(seq 1 90); do
  rm -f "$TMP_BASELINE"
  if curl -sf --max-time 30 -o "$TMP_BASELINE" "$url"; then
    fetched=true
    break
  fi
  sleep 2
done
if [[ "$fetched" != true ]]; then
  "${COMPOSE[@]}" logs api | tail -50
  echo "nestjs 未能在 180s 内就绪" >&2
  exit 1
fi
python3 -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$TMP_BASELINE"
mv "$TMP_BASELINE" "$OUT/nestjs-baseline.json"
mv "$STAGE/validation.json" "$OUT/validation.json"
mv "$STAGE/validation-cases.json" "$CASES_DIR/validation-cases.json"

cat >"$OUT/SOURCE" <<SOURCE
repository=xingyunqi-tech/nestjs-template
branch=${NESTJS_BRANCH}
commit=${NESTJS_REF}
validation_script=${NESTJS_SCRIPT_REF}
environment=NODE_ENV=development FILE_DRIVER=local
SOURCE

echo "==> 已更新 openapi/、tests/contract/fixtures/validation-cases.json"
