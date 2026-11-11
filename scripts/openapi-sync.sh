#!/usr/bin/env bash
# 在 nestjs-template 固定提交上导出 openapi/ 基线。
#
# 变量：
#   NESTJS_DIR     nestjs-template 本地仓库（默认 ../nestjs-template）
#   NESTJS_REF     导出所用提交（默认取 openapi/SOURCE 的 commit）
#   NESTJS_BRANCH  记入 SOURCE 的分支名（默认 release/exam-prep）
#   NESTJS_PORT    导出期间 nestjs 映射到宿主的端口（默认 3001）
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

WORKTREE="$(mktemp -d)"
export NESTJS_IMAGE="fastapi-temp/nestjs-ref:${NESTJS_REF:0:12}"
export NESTJS_ENV_FILE="$WORKTREE/env-example-relational"
TMP_BASELINE="$OUT/nestjs-baseline.json.tmp"
COMPOSE=(docker compose -p nestjs-openapi-sync -f "$ROOT/docker/nestjs.compose.yaml")

cleanup() {
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  git -C "$NESTJS_DIR" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
  rm -rf "$WORKTREE" "$TMP_BASELINE"
}
trap cleanup EXIT

echo "==> nestjs-template ${NESTJS_REF}"
git -C "$NESTJS_DIR" worktree add --detach "$WORKTREE" "$NESTJS_REF" >/dev/null

echo "==> 构建镜像 ${NESTJS_IMAGE}"
docker build --target production -t "$NESTJS_IMAGE" "$WORKTREE"

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

cat >"$OUT/SOURCE" <<SOURCE
repository=xingyunqi-tech/nestjs-template
branch=${NESTJS_BRANCH}
commit=${NESTJS_REF}
environment=NODE_ENV=development FILE_DRIVER=local
SOURCE

echo "==> 已更新 openapi/nestjs-baseline.json、openapi/SOURCE"
