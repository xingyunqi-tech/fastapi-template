# CLI

`app` 命令由 `pyproject.toml` 的 `[project.scripts]` 注册（`app.cli:main`，Typer）。本地通过 `uv run --frozen app ...` 调用，镜像内直接 `app ...`。配置读取方式与 API 相同（环境变量 + 工作目录 `.env`）。

```bash
uv run --frozen app --help
```

| 命令 | 说明 | Make |
|---|---|---|
| `app migrate [--check]` | 在 `DATABASE_SCHEMA` 中升级本角色的分支：签发方 `heads`，验证方依次 `platform@head`、`modules@head`；未设置 `DATABASE_URL` 时直接退出；`--check` 只检查是否已升级到目标版本且 schema 与模型一致，否则退出码 1 | `make migrate` |
| `app seed` | 仅签发方：无 active admin 时创建管理员，见 [auth.md](auth.md#种子数据) | `make seed` |
| `app worker [--workers N]` | 启动 Taskiq worker（默认 1 个进程）；未设置 `REDIS_URL` 时提示无需 worker；Redis 不可达，或 `OTEL_METRICS_EXPORTER=prometheus` 时 `--workers` 大于 1，退出码 1 | – |
| `app scheduler` | 启动 Taskiq scheduler，按 cron 投递定时任务 | – |
| `app keys generate` | 输出新的 Ed25519 私钥与公钥（PEM），不读取配置；轮换步骤见 [deploy.md](deploy.md#签名密钥) | `make keys` |
| `app users cleanup <id>` | 仅签发方：重新执行该用户的注销数据清理 | – |
| `app generate resource <Name>` | 按 `notes` 的结构生成 CRUD 模块骨架（本人可见、`<name>:read` / `<name>:write` 权限、注销清理器、集成测试） | `make generate-resource name=<Name>` |
| `app openapi export [--check]` | 按代码导出 `openapi/openapi.json`；`--check` 不一致时退出码 1 | `make openapi` |

`python -m app` 启动 API（Granian），对应 `make serve`。

## worker 与 scheduler

| 进程 | 消费 / 投递 |
|---|---|
| worker | Redis Stream `taskiq:mail`、`taskiq:account-cleanup`、`taskiq:scheduler`；配置了数据库且 `EVENTS_TRANSPORT=redis_stream` 的验证方另消费 `EVENTS_REDIS_STREAM` 中的 `user.deleted` |
| scheduler | `scheduler:cleanup-old-sessions`（`0 3 * * *` UTC）、`scheduler:account-cleanup-compensation`（`25 * * * *` Asia/Shanghai），二者均为 `issuer_only`，验证方不调度 |

多实例运行 scheduler 时，任务执行由 `{OTEL_SERVICE_NAME}:lock:cron:<name>` 互斥。worker 的指标由其自身的 `OTEL_EXPORTER_PROMETHEUS_PORT` 输出。

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
| `models.py` | ORM（`modules` 分支）：UUIDv7 `id`、`name`、`created_at`、`updated_at`、`deleted_at`（软删除） |
| `repository.py` / `service.py` | 数据访问与业务层 |
| `schemas.py` | 请求与响应模型（按 Pydantic 约束校验） |
| `router.py` | `POST` / `GET` 列表 / `GET :id` / `PUT :id` / `DELETE :id`，`auth=Auth.JWT` |
| `tests/integration/test_<复数>.py` | 集成测试（未迁移时在用例事务内建表，未注册时把路由挂到测试应用） |

表名与已有模型冲突、或目标文件已存在且未加 `--force` 时报错退出；生成后对新文件执行 ruff。生成后命令会打印后续步骤：

1. `src/app/factory.py` 中导入路由与 `account_cleaner`，在 `mount_routes` 的数据库分支中 `include_routes(app, <module>.router, API_PREFIX)`
2. `src/app/infrastructure/database/models.py` 中导入新模型
3. `uv run --frozen alembic revision --autogenerate -m "add <table>" --head modules@head`，检查迁移后 `make migrate`
4. `uv run --frozen pytest tests/integration/test_<复数>.py`

## 脚本

| 脚本 | 用途 |
|---|---|
| `scripts/check_commit_msg.py` | commit-msg 钩子 |
| `scripts/audit_severity.py` | 过滤 pip-audit 结果（`make security`） |
