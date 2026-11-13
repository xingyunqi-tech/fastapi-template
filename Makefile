.DEFAULT_GOAL := help
SHELL := /bin/bash

UV ?= uv
RUN := $(UV) run --frozen
PYTEST := $(RUN) pytest

.PHONY: help
help: ## 列出可用目标
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: install
install: ## 安装依赖与 Git 钩子
	$(UV) sync --frozen
	$(RUN) prek install

.PHONY: dev
dev: ## 本地开发服务（热重载，端口 APP_PORT，默认 3000）
	$(RUN) fastapi dev --port $$($(RUN) python -c 'from app.config import get_settings; print(get_settings().app.port)')

.PHONY: serve
serve: ## 以生产方式启动（Granian）
	$(RUN) python -m app

.PHONY: fmt
fmt: ## 格式化并自动修复
	$(RUN) ruff format
	$(RUN) ruff check --fix

.PHONY: lint
lint: ## ruff check、ruff format --check、typos
	$(RUN) ruff check
	$(RUN) ruff format --check
	$(RUN) typos
	$(RUN) python scripts/gen_schemas.py --check

.PHONY: typecheck
typecheck: ## mypy（门禁）+ ty（试运行，不阻塞）
	$(RUN) mypy
	-$(RUN) ty check

.PHONY: test-unit
test-unit: ## 单元测试
	$(PYTEST) -m unit -n auto

.PHONY: check
check: lint typecheck test-unit ## lint + 类型检查 + 单元测试

.PHONY: test
test: ## 单元 + 集成 + 契约测试（含覆盖率）
	$(PYTEST) -m "unit or integration or contract" -n auto --cov --cov-report=term --cov-report=xml

.PHONY: test-e2e
test-e2e: ## e2e 测试：ci 组合以生产镜像起完整服务栈，结束后 down -v（E2E_KEEP_STACK=1 保留）
	$(PYTEST) -m e2e

.PHONY: test-contract
test-contract: ## 契约测试（UPDATE_CONTRACT_DOC=1 时重新生成 docs/contract.md）
	$(PYTEST) -m contract -n auto

.PHONY: test-parity
test-parity: ## parity 测试（PARITY_PYTHON_URL / PARITY_NESTJS_URL，默认 3000 / 3001）
	$(PYTEST) -m parity

.PHONY: migrate
migrate: ## 执行数据库迁移（DB_SCHEMA_OWNER=external 时跳过）
	$(RUN) app migrate

.PHONY: seed
seed: ## 写入种子数据（角色、权限、行政区划、配置、初始账号）
	$(RUN) app seed

.PHONY: generate-resource
generate-resource: ## 生成 CRUD 模块骨架（make generate-resource name=Product）
	@test -n "$(name)" || { echo "用法：make generate-resource name=<Name>"; exit 1; }
	$(RUN) app generate resource "$(name)"

.PHONY: infra
infra: ## 启动本地 Postgres / Redis / Mailpit（docker compose）
	docker compose -f docker/infra.compose.yaml up -d --wait

IMAGE ?= fastapi-temp:local
COMPOSE_DEV := docker compose -f docker/docker-compose.base.yaml -f docker/docker-compose.redis.yaml -f docker/docker-compose.postgres.yaml -f docker/docker-compose.dev.yaml

.PHONY: docker-build
docker-build: ## 构建生产镜像（IMAGE，默认 fastapi-temp:local）
	docker build --target production -t $(IMAGE) .

.PHONY: up
up: ## docker compose 起开发服务栈（api / worker / scheduler / migrate + 依赖，develop.watch）
	$(COMPOSE_DEV) up --build --watch

.PHONY: down
down: ## 停止开发服务栈
	$(COMPOSE_DEV) down

.PHONY: openapi-sync
openapi-sync: ## 从 nestjs-template 固定提交导出 openapi/ 基线
	scripts/openapi-sync.sh

.PHONY: security
security: ## pip-audit 生产依赖（high 及以上失败）
	@tmp="$$(mktemp -d)" && trap 'rm -rf "$$tmp"' EXIT && \
	$(UV) export --frozen --no-dev --no-emit-project --format requirements-txt -q -o "$$tmp/requirements.txt" && \
	{ uvx pip-audit==2.10.1 -r "$$tmp/requirements.txt" --disable-pip --require-hashes --progress-spinner off \
		-f json -o "$$tmp/audit.json" || test -s "$$tmp/audit.json"; } && \
	$(UV) run --script scripts/audit_severity.py "$$tmp/audit.json"

.PHONY: workflow-lint
workflow-lint: ## actionlint + zizmor（min-severity medium）
	actionlint
	uvx zizmor==1.32.0 --min-severity medium .

.PHONY: ci
ci: check test security workflow-lint ## 本地运行 CI 门禁

.PHONY: clean
clean: ## 清理缓存与构建产物
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml .coverage .coverage.* dist build
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
