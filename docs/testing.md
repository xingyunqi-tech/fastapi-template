# 测试

pytest 9 + pytest-asyncio（`asyncio_mode = "auto"`）+ pytest-xdist。配置：`--strict-markers --strict-config`、`xfail_strict`、`filterwarnings = error`；覆盖率 branch 模式，`fail_under = 85`。

## 分层

marker 按 `tests/` 下的一级目录自动添加，无需手写。

| 目录 / marker | 依赖 | 运行 | 内容 |
|---|---|---|---|
| `unit` | 无 | `make test-unit` | 纯函数、配置、路由与信封、校验引擎、生成器、提交信息检查等 |
| `integration` | Docker（testcontainers） | `make test` | 每个模块的 HTTP 行为、数据层、限流、健康检查、指标、文件、队列与定时任务 |
| `contract` | 无 | `make test-contract` | OpenAPI 与 `validation.json` 契约比对 |
| `parity` | 本服务 + nestjs 同时运行 | `make test-parity` | 同请求打两个后端并比对 |
| `e2e` | Docker Compose | `make test-e2e` | 生产镜像完整服务栈黑盒测试 |

`make test` = unit + integration + contract（`-n auto`，输出 `coverage.xml`）；`make check` 只跑 unit。

## 环境隔离

- 会话开始与每个用例前清除全部配置相关环境变量，写入固定的测试密钥（`THROTTLE_ENABLED=false`），并 `chdir` 到临时目录，工作目录下的 `.env` 不生效
- 需要生产配置的用例使用 `tests.conftest.use_production`

## 集成测试

| fixture | 说明 |
|---|---|
| `pg_server` / `redis_url` | 会话级 testcontainers：`postgres:18.6-alpine`、`redis:8.10.3-alpine` |
| `template_db` | 在模板库上执行 `alembic upgrade head` 与 seed |
| `worker_db` | 每个 xdist worker 用 `CREATE DATABASE … TEMPLATE` 克隆一份 |
| `app` / `client` | 回滚模式：用例在外层事务内执行，`Database.bound_to(connection)` 让提交只释放 SAVEPOINT，结束后整体回滚 |
| `committed_app` | 真实提交模式：克隆独立库并连接 Redis；用于 refresh CAS 并发、提交后副作用、队列投递等，用例标 `@pytest.mark.committed` |

- 测试应用的出站 HTTP 一律拒绝（`MockTransport` 抛错），微信、支付宝、短信等外部调用需在用例内替换
- testcontainers 起不来时用例失败，不跳过
- macOS 上使用 Docker Desktop 时自动设置 `DOCKER_HOST`

```bash
uv run --frozen pytest tests/integration/test_auth.py -n auto
uv run --frozen pytest -m committed
```

## 契约测试

`tests/contract/test_openapi_contract.py`：

- `/docs-json` 与 `openapi/nestjs-baseline.json` 逐字节一致
- FastAPI 内部 OpenAPI 的每个已实现 operation 必须存在于基线，且 operationId、tag、参数、请求体 content-type 与 schema、成功响应 schema、`security` 一致
- 已实现 operation 的相对顺序与基线一致
- 路由用到的 DTO 均能由校验引擎按 `validation.json` 处理
- `openapi/SOURCE` 记录固定提交；`validation.json` 不含未支持的特性
- `FILE_DRIVER=s3` 与 local 一致，`s3-presigned` 只在白名单中的上传接口不同
- `docs/contract.md` 与实际一致

`tests/contract/test_validation_engine.py`：用 `tests/contract/fixtures/validation-cases.json`（nestjs ValidationPipe 计算的期望结果）逐例比对 422 输出。

`docs/contract.md` 由测试生成，不手改；「与 nestjs 的行为差异」一节为手写，生成时保留：

```bash
UPDATE_CONTRACT_DOC=1 make test-contract
```

## parity

`tests/parity` 用 httpx 对两个后端发送相同请求，比对状态码、响应体（忽略 id、时间、令牌等易变字段）与 `Content-Type`。限流头（`X-RateLimit-*`、`Retry-After`）只要求两侧出现的头集合一致、取值均为整数，按值比对的只有 `X-RateLimit-Limit`；任一侧返回 429 即失败，429 的 `Retry-After` 由专门的用例检查。同一套用例对 nestjs 也必须通过。

前提：两侧使用同一份 seed、`THROTTLE_ENABLED=true`、`TRUST_PROXY=1`、`REDIS_ENABLED=true`（本服务另起 worker）。

| 变量 | 默认 | 说明 |
|---|---|---|
| `PARITY_PYTHON_URL` / `PARITY_NESTJS_URL` | `http://localhost:3000` / `http://localhost:3001` | 两个后端地址 |
| `PARITY_PYTHON_PG_CONTAINER` / `PARITY_NESTJS_PG_CONTAINER` | `fastapi-temp-infra-postgres-1` / `parity-nestjs-postgres-1` | 用 `docker exec psql` 准备 / 清理数据的 Postgres 容器名 |
| `PARITY_PYTHON_REDIS_URL` | `redis://localhost:6379/0` | 读取本服务短信验证码 |
| `PARITY_NESTJS_REDIS_URL` / `PARITY_NESTJS_REDIS_CONTAINER` | 空 / `parity-nestjs-redis-1` | 读取 nestjs 短信验证码 |

本地按 CI `parity` job 的步骤准备，默认值即对应以下组合：nestjs 用 `docker compose -p parity-nestjs -f scripts/nestjs/nestjs.compose.yaml up -d --wait`（`NESTJS_IMAGE`、`NESTJS_ENV_FILE` 必填，api 映射到 3001）；本服务 `make infra && make migrate && make seed && make serve`（Postgres 容器 `fastapi-temp-infra-postgres-1`，Redis 在 6379），另起 `uv run --frozen app worker`。

CI 的 parity job 还会把 nestjs 的 `test/user/auth.e2e-spec.ts` 指向本服务运行，并比对两边迁移后的表结构与 `db/nestjs-schema.sql`。

## e2e

`make test-e2e` 以 base + postgres + redis + ci 组合（项目名 `E2E_PROJECT`，默认 `fastapi-e2e`）`up --build --wait` 起完整服务栈，宿主端口随机分配，结束后 `down --volumes`；启动或用例失败时输出最近 200 行日志。`E2E_KEEP_STACK=1` 保留服务栈；设置 `CI` 时失败的服务栈不 down，由 workflow 收集容器日志。

覆盖：`/health/ready`、邮箱注册全流程（Mailpit 收信）、公开 / 私有文件往返、限流头与 429、`/metrics` 同时包含 api 与 worker 的样本、`/docs-json` 逐字节一致。

## 编写约定

- 用例名只陈述行为
- 新模块至少有集成测试覆盖成功路径与各类错误形状；涉及契约的接口补 parity 用例
- 依赖真实提交的场景用 `committed_app`，其余用回滚模式
