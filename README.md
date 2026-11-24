# fastapi-temp

独立的 Python / FastAPI 后端模板：可单独部署，也可在 monorepo 中作为签发方（身份服务）或验证方（业务服务）。对外行为以 [docs/platform-conventions.md](docs/platform-conventions.md) 为准，该约定与同平台的 Go、Spring Boot 模板共用；OpenAPI 由代码生成并入库为 `openapi/openapi.json`。改造方案见 [TEMPLATE_PLAN.md](TEMPLATE_PLAN.md)。

| 项 | 选型 |
|---|---|
| 语言 / 包管理 | Python 3.15（`.python-version`）、uv 0.13 |
| Web | FastAPI + Starlette，生产服务器 Granian |
| 数据 | PostgreSQL 18、SQLAlchemy 2.1 async + asyncpg、Alembic |
| 缓存 / 锁 / 限流 / 队列 | Redis 8、redis-py asyncio、Taskiq + taskiq-redis（Redis Stream） |
| 认证 | PyJWT（EdDSA）access + 不透明 refresh 轮换、JWKS、pwdlib（argon2id） |
| 可观测性 | structlog、prometheus-client、OpenTelemetry、Sentry |
| CLI | Typer（`app` 命令） |
| 质量 | Ruff、mypy strict（门禁）+ ty（试运行）、pytest + testcontainers、schemathesis、prek 钩子 |

## 功能

- 统一响应信封与 errorKey 错误码、校验错误转换、i18n（zh / en）、Request ID、CORS、安全头、限流（GCRA）、幂等键
- 认证：邮箱注册登录、邮箱验证、找回与重置密码；短信验证码登录与手机号绑定；微信、支付宝登录（配置后启用）；EdDSA access + refresh 轮换（30 秒宽限期、重放检测）与吊销；会话（设备）管理；角色与权限
- 用户管理、文件上传（`local` / `s3`，按内容识别类型，私有文件签名地址）
- `notes` 示例模块（本人可见的 CRUD），`app generate resource` 按同样结构生成新模块
- 健康检查、Prometheus 指标、审计、账号注销清理与 `user.deleted` 事件、邮件（中英模板）、定时任务
- 验证方模式：从签发方 JWKS 取公钥校验令牌，按已配置的数据库与存储挂载模块，消费 `user.deleted` 清理本服务数据
- `/openapi.json`（OpenAPI 3.1）与 `/docs`（Scalar）；`DOCS_ENABLED` 控制，生产默认关闭；`make openapi` 导出到 `openapi/openapi.json`

## 快速开始

前置：Python 3.15、uv 0.13+、Docker。

```bash
make install                  # uv sync + 安装 prek 钩子
cp .env.example .env          # 要单独运行 worker 时设置 REDIS_URL
make keys                     # 私钥填入 .env 的 AUTH_SIGNING_KEY
make infra                    # Postgres 18 :5432、Redis 8 :6379、Mailpit（SMTP :1025，UI :8025）、MinIO :9000
make migrate                  # 签发方 heads，验证方 platform@head 与 modules@head
make seed                     # 管理员账号
make dev                      # 热重载，http://localhost:3000
uv run --frozen app worker    # 队列 worker（需 REDIS_URL）
uv run --frozen app scheduler # 定时任务调度
```

- 生产方式启动 API：`make serve`（`python -m app`，Granian，进程数取 `WEB_CONCURRENCY`）
- 未设置 `REDIS_URL` 时任务在 API 进程内执行，无需 worker；生产必须设置 `REDIS_URL`
- 非生产环境 seed 账号：`admin@example.com` / `Secret00`

## 签发方与验证方

`AUTH_SIGNING_KEY` 与 `AUTH_JWKS_URL` 恰好设置一个，决定本服务的角色：

| | 签发方 | 验证方 |
|---|---|---|
| 令牌 | `AUTH_SIGNING_KEY` 签发，`/.well-known/jwks.json` 公开公钥 | `AUTH_JWKS_URL` 取签发方公钥，只校验 |
| 必需依赖 | `DATABASE_URL`、`STORAGE_DRIVER`、`MAIL_*`（生产） | 无；`DATABASE_URL`、`STORAGE_DRIVER` 按需配置 |
| 挂载 | identity 全部接口、audit、files、notes | 有数据库时 audit、notes；再配置存储时 files；健康检查与指标始终挂载 |
| 迁移 | `app migrate` 升级全部分支 | 只升级 platform + modules，`DATABASE_SCHEMA` 与其他服务不同 |
| 注销 | 清理任务发布 `user.deleted` | `app worker` 消费 `user.deleted`，执行本服务的清理器 |

monorepo 部署清单见 [docs/deploy.md](docs/deploy.md#monorepo-部署)。

## Make 目标

| 目标 | 说明 |
|---|---|
| `make install` | 安装依赖与 Git 钩子 |
| `make dev` | 本地开发服务（`fastapi dev`，端口 `APP_PORT`，默认 3000） |
| `make serve` | 以生产方式启动（Granian） |
| `make fmt` | `ruff format` + `ruff check --fix` |
| `make lint` | `ruff check`、`ruff format --check`、`typos` |
| `make typecheck` | mypy（门禁）+ ty（不阻塞） |
| `make test-unit` | 单元测试 |
| `make check` | lint + typecheck + test-unit |
| `make test` | 单元 + 约定 + 集成测试（含覆盖率，集成测试需 Docker） |
| `make test-conventions` | 约定测试（探针路由） |
| `make test-api` | schemathesis：按 `openapi/openapi.json` 对运行中的服务发请求（需 Docker） |
| `make openapi` | 导出 `openapi/openapi.json` |
| `make test-e2e` | e2e：ci 组合起完整服务栈后黑盒测试 |
| `make migrate` | 升级本角色的迁移分支 |
| `make seed` | 写入种子数据 |
| `make keys` | 生成 Ed25519 签名密钥对 |
| `make generate-resource name=Product` | 生成 CRUD 模块骨架 |
| `make infra` | 启动本地 Postgres / Redis / Mailpit / MinIO |
| `make docker-build` | 构建生产镜像（`IMAGE`，默认 `fastapi-temp:local`） |
| `make up` / `make down` | 起 / 停 compose 开发服务栈（`develop.watch`） |
| `make security` | pip-audit 生产依赖（high 及以上失败） |
| `make workflow-lint` | actionlint + zizmor（medium） |
| `make bench` | k6 压测（起 bench 栈、跑全部场景后删除），见 [docs/benchmark.md](docs/benchmark.md) |
| `make ci` | check + test + test-api + security + workflow-lint |
| `make clean` | 清理缓存与构建产物 |

`make help` 列出带说明的目标。

## Compose

`docker/` 下的文件按组合使用，详见 [docs/deploy.md](docs/deploy.md)。

| 文件 | 用途 |
|---|---|
| `infra.compose.yaml` | 本地依赖：Postgres、Redis、Mailpit、MinIO（`make infra`） |
| `docker-compose.base.yaml` | 共用：mailpit、migrate（一次性）、api |
| `docker-compose.postgres.yaml` | Postgres；Adminer（profile `tools`） |
| `docker-compose.redis.yaml` | Redis、worker、scheduler；RedisInsight（profile `tools`） |
| `docker-compose.dev.yaml` | development 镜像 + `develop.watch`、测试签名密钥（`make up`） |
| `docker-compose.test.yaml` | development 镜像，挂载 `src/`、`tests/` |
| `docker-compose.ci.yaml` | 生产镜像、`APP_ENV=production`、随机宿主端口（e2e） |
| `docker-compose.ecs.yaml` | 服务器部署（GHCR / TCR 镜像，配合 `deploy/manage.sh`） |

## 文档

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 目录结构、请求链路、进程模型、基础设施 |
| [docs/platform-conventions.md](docs/platform-conventions.md) | 跨语言平台约定（对外行为的唯一依据） |
| [docs/auth.md](docs/auth.md) | 登录方式、JWT 与 JWKS、会话、RBAC、鉴权声明 |
| [docs/database.md](docs/database.md) | 连接与 schema、模型、事务、迁移分支、seed |
| [docs/deploy.md](docs/deploy.md) | 镜像、compose、密钥轮换、monorepo 部署、manage.sh、GitHub Actions |
| [docs/testing.md](docs/testing.md) | 测试分层与运行方式 |
| [docs/cli.md](docs/cli.md) | `app` 命令与代码生成器 |
| [docs/benchmark.md](docs/benchmark.md) | k6 压测方法与结果 |
| [CLAUDE.md](CLAUDE.md) | AI 助手约定 |
