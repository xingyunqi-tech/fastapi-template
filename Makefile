.DEFAULT_GOAL := help
SHELL := /bin/bash

UV ?= uv
RUN := $(UV) run --frozen
PYTEST := $(RUN) pytest

.PHONY: help
help: ## 列出可用目标
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

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
test-e2e: ## e2e 测试
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

.PHONY: infra
infra: ## 启动本地 Postgres / Redis / Mailpit（docker compose）
	docker compose -f docker/infra.compose.yaml up -d --wait

.PHONY: openapi-sync
openapi-sync: ## 从 nestjs-template 固定提交导出 openapi/ 基线
	scripts/openapi-sync.sh

.PHONY: ci
ci: check test ## 本地运行 CI 门禁

.PHONY: clean
clean: ## 清理缓存与构建产物
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml .coverage .coverage.* dist build
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
