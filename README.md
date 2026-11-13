# fastapi-temp

FastAPI 后端模板。HTTP API 契约与 [nestjs-template](https://github.com/xingyunqi-tech/nestjs-template)（`release/exam-prep` 分支 `02fdb4d`，见 `openapi/SOURCE`）一致：vite-vue3-temp、uniapp-template 由 `/docs-json` 经 Orval 生成的前端可在两个后端之间无改动切换。改造方案见 [TEMPLATE_PLAN.md](TEMPLATE_PLAN.md)。

| 项 | 选型 |
|---|---|
| 语言 / 包管理 | Python 3.15（`.python-version`）、uv 0.13 |
| Web | FastAPI + Starlette，生产服务器 Granian |
| 数据 | PostgreSQL 18、SQLAlchemy 2.1 async + asyncpg、Alembic |
| 缓存 / 锁 / 限流 / 队列 | Redis 8、redis-py asyncio、Taskiq + taskiq-redis（Redis Stream） |
| 认证 | PyJWT 双令牌 + 会话、pwdlib（argon2id / bcrypt） |
| 可观测性 | structlog、prometheus-client、OpenTelemetry、Sentry |
| CLI | Typer（`app` 命令） |
| 质量 | Ruff、mypy strict（门禁）+ ty（试运行）、pytest + testcontainers、prek 钩子 |

## 功能

- 统一响应信封、错误翻译（i18n en / zh）、class-validator 等价校验、Request ID、CORS、安全头、限流（GCRA）
- 认证：邮箱、手机号密码、短信验证码、微信（小程序 / App / 手机号）、支付宝；JWT access + refresh 轮换；会话（设备）管理；RBAC
- 用户管理、文件上传（`local` / `s3` / `s3-presigned`）与私有文件
- 通用模块：config、regions、notifications、addresses、favorites、feedbacks、consents、app-profile
- 健康检查、Prometheus 指标、审计、运营告警、账号注销清理、邮件（中英模板）、定时任务
- `/docs-json` 逐字节输出 nestjs 完整 OpenAPI 基线（含本模板未实现的考试业务接口，请求返回 404 信封），`/docs` 为 Scalar 页面；`SWAGGER_ENABLED` 控制，生产默认关闭

## 快速开始

前置：Python 3.15、uv 0.13+、Docker。

```bash
make install                  # uv sync + 安装 prek 钩子
cp .env.example .env          # 要单独运行 worker 时把 REDIS_ENABLED 改为 true
make infra                    # Postgres 18 :5432、Redis 8 :6379、Mailpit（SMTP :1025，UI :8025）
make migrate                  # alembic upgrade head
make seed                     # 角色、状态、权限、行政区划、配置、初始账号
make dev                      # 热重载，http://localhost:3000
uv run --frozen app worker    # 队列 worker（需 REDIS_ENABLED=true）
uv run --frozen app scheduler # 定时任务调度
```

- 生产方式启动 API：`make serve`（`python -m app`，Granian，进程数取 `WEB_CONCURRENCY`）
- `REDIS_ENABLED=false` 时任务在 API 进程内执行，无需 worker；生产强制 `REDIS_ENABLED=true`
- 非生产环境 seed 账号：`admin@example.com` / `john.doe@example.com`，密码 `Secret00`（接口传 MD5）

## Make 目标

| 目标 | 说明 |
|---|---|
| `make install` | 安装依赖与 Git 钩子 |
| `make dev` | 本地开发服务（`fastapi dev`，端口 `APP_PORT`，默认 3000） |
| `make serve` | 以生产方式启动（Granian） |
| `make fmt` | `ruff format` + `ruff check --fix` |
| `make lint` | `ruff check`、`ruff format --check`、`typos`、`scripts/gen_schemas.py --check` |
| `make typecheck` | mypy（门禁）+ ty（不阻塞） |
| `make test-unit` | 单元测试 |
| `make check` | lint + typecheck + test-unit |
| `make test` | 单元 + 集成 + 契约测试（含覆盖率，集成测试需 Docker） |
| `make test-contract` | 契约测试；`UPDATE_CONTRACT_DOC=1` 时重新生成 `docs/contract.md` |
| `make test-e2e` | e2e：ci 组合起完整服务栈后黑盒测试 |
| `make test-parity` | 与 nestjs 同请求比对（`PARITY_PYTHON_URL` / `PARITY_NESTJS_URL`，默认 3000 / 3001） |
| `make migrate` | 执行迁移（`DB_SCHEMA_OWNER=external` 时跳过） |
| `make seed` | 写入种子数据 |
| `make generate-resource name=Product` | 生成 CRUD 模块骨架 |
| `make infra` | 启动本地 Postgres / Redis / Mailpit |
| `make docker-build` | 构建生产镜像（`IMAGE`，默认 `fastapi-temp:local`） |
| `make up` / `make down` | 起 / 停 compose 开发服务栈（`develop.watch`） |
| `make openapi-sync` | 从 nestjs-template 固定提交导出 `openapi/` 等基线 |
| `make security` | pip-audit 生产依赖（high 及以上失败） |
| `make workflow-lint` | actionlint + zizmor（medium） |
| `make ci` | check + test + security + workflow-lint |
| `make clean` | 清理缓存与构建产物 |

`make help` 列出带说明的目标。

## Compose

`docker/` 下的文件按组合使用，详见 [docs/deploy.md](docs/deploy.md)。

| 文件 | 用途 |
|---|---|
| `infra.compose.yaml` | 本地依赖：Postgres、Redis、Mailpit（`make infra`） |
| `docker-compose.base.yaml` | 共用：mailpit、migrate（一次性）、api |
| `docker-compose.postgres.yaml` | Postgres；Adminer（profile `tools`） |
| `docker-compose.redis.yaml` | Redis、worker、scheduler；RedisInsight（profile `tools`） |
| `docker-compose.dev.yaml` | development 镜像 + `develop.watch`（`make up`） |
| `docker-compose.test.yaml` | development 镜像，挂载 `src/`、`tests/` |
| `docker-compose.ci.yaml` | 生产镜像、`APP_ENV=production`、随机宿主端口（e2e） |
| `docker-compose.ecs.yaml` | 服务器部署（GHCR / TCR 镜像，配合 `deploy/manage.sh`） |

## 文档

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 目录结构、请求链路、进程模型、基础设施 |
| [docs/auth.md](docs/auth.md) | 登录方式、JWT 与会话、RBAC、鉴权声明 |
| [docs/database.md](docs/database.md) | 模型与迁移、事务、共库、seed |
| [docs/deploy.md](docs/deploy.md) | 镜像、compose、manage.sh、GitHub Actions |
| [docs/testing.md](docs/testing.md) | 测试分层与运行方式 |
| [docs/cli.md](docs/cli.md) | `app` 命令与代码生成器 |
| [docs/contract.md](docs/contract.md) | 未实现接口清单、白名单、与 nestjs 的行为差异（生成） |
| [CLAUDE.md](CLAUDE.md) | AI 助手约定 |
