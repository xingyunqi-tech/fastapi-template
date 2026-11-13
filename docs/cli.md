# CLI

`app` 命令由 `pyproject.toml` 的 `[project.scripts]` 注册（`app.cli:main`，Typer）。本地通过 `uv run --frozen app ...` 调用，镜像内直接 `app ...`。配置读取方式与 API 相同（环境变量 + 工作目录 `.env`）。

```bash
uv run --frozen app --help
```

| 命令 | 说明 | Make |
|---|---|---|
| `app migrate` | `alembic upgrade head`；`DB_SCHEMA_OWNER=external` 时跳过 | `make migrate` |
| `app seed` | 写入角色、状态、权限、行政区划、配置与初始账号，已存在的不覆盖，见 [database.md](database.md#seed) | `make seed` |
| `app worker [--workers N]` | 启动 Taskiq worker（默认 1 个进程）；`REDIS_ENABLED=false` 或 Redis 不可达时退出码 1 | – |
| `app scheduler` | 启动 Taskiq scheduler，按 cron 投递定时任务 | – |
| `app permissions clear-cache` | 删除角色权限缓存 `permissions:v2:role-map` | – |
| `app generate resource <Name>` | 生成 CRUD 模块骨架 | `make generate-resource name=<Name>` |

`python -m app` 启动 API（Granian），对应 `make serve`。

## worker 与 scheduler

| 进程 | 消费 / 投递 |
|---|---|
| worker | Redis Stream `taskiq:mail`、`taskiq:notification`、`taskiq:account-cleanup`、`taskiq:scheduler` |
| scheduler | `scheduler:cleanup-old-sessions`（`0 3 * * *` UTC）、`scheduler:cleanup-read-notifications`（`0 4 * * *` UTC）、`scheduler:account-cleanup-compensation`（`25 * * * *` Asia/Shanghai） |

多实例运行 scheduler 时，任务执行由 `lock:scheduler:<name>` 互斥。worker 需与 API 共用 `PROMETHEUS_MULTIPROC_DIR`，其指标才会出现在 API 的 `/metrics`。

## 代码生成器

```bash
make generate-resource name=Product
uv run --frozen app generate resource product-category --plural product-categories
```

| 参数 | 说明 |
|---|---|
| `name` | 资源名（单数），如 `Product`、`product-category` |
| `--plural` | 复数形式，默认按英文规则推导 |
| `--root` | 项目根目录，默认当前仓库 |
| `--force` | 覆盖已存在的文件 |

生成内容（模板在 `src/app/cli/templates/resource/`）：

| 文件 | 内容 |
|---|---|
| `src/app/modules/<复数>/__init__.py` | 包 |
| `models.py` | ORM：`id`、`name`、`createdAt`、`updatedAt`、`deletedAt`（软删除） |
| `repository.py` / `service.py` | 数据访问与业务层 |
| `schemas.py` | 请求与响应模型（按 Pydantic 约束校验） |
| `router.py` | `POST` / `GET` 列表 / `GET :id` / `PUT :id` / `DELETE :id`，`auth=Auth.JWT` |
| `tests/integration/test_<复数>.py` | 集成测试（未迁移时在用例事务内建表，未注册时把路由挂到测试应用） |

表名与已有模型冲突、或目标文件已存在且未加 `--force` 时报错退出；生成后对新文件执行 ruff。生成后命令会打印后续步骤：

1. `src/app/factory.py` 中导入路由并 `include_routes(app, <module>.router, api_prefix)`
2. `src/app/infrastructure/database/models.py` 中导入新模型
3. `uv run --frozen alembic revision --autogenerate -m "add <table>"`，检查迁移后 `make migrate`
4. `uv run --frozen pytest tests/integration/test_<复数>.py`

注意：契约测试要求每个已注册的 operation 都存在于 `openapi/nestjs-baseline.json`。在 `factory.py` 注册基线之外的接口会使 `make test-contract` 失败，契约变更的流程见 [CLAUDE.md](../CLAUDE.md)。

## 脚本

| 脚本 | 用途 |
|---|---|
| `scripts/openapi-sync.sh`（`make openapi-sync`） | 在 nestjs-template 固定提交上导出 `openapi/`（基线、`validation.json`、`nest_handlers.json`、`SOURCE`）、`db/nestjs-schema.sql`、`tests/contract/fixtures/validation-cases.json`；需要 Docker、git、uv |
| `scripts/gen_schemas.py` | 由基线生成 `src/app/contract/schemas.py`；`--check` 比较（`make lint` 执行） |
| `scripts/schema_snapshot.py` | 输出表结构快照（`uv run --script`） |
| `scripts/gen_validation_cases.py` | 由 `validation.json` 生成校验用例 |
| `scripts/gen_nest_handlers.py` | 生成 operationId → nestjs `Controller-handler` 映射（限流键） |
| `scripts/check_commit_msg.py` | commit-msg 钩子 |
| `scripts/audit_severity.py` | 过滤 pip-audit 结果（`make security`） |

`openapi-sync.sh` 变量：

| 变量 | 默认 |
|---|---|
| `NESTJS_DIR` | `../nestjs-template` |
| `NESTJS_REF` | `openapi/SOURCE` 的 `commit` |
| `NESTJS_BRANCH` | `release/exam-prep` |
| `NESTJS_SCRIPT_REF` | `openapi/SOURCE` 的 `validation_script`，否则 `NESTJS_BRANCH` |
| `NESTJS_PORT` | `3001`（被占用时退出） |
| `NESTJS_SYNC_PROJECT` | `nestjs-openapi-sync-<仓库目录名>` |
