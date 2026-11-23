# 测试

pytest 9 + pytest-asyncio（`asyncio_mode = "auto"`）+ pytest-xdist。配置：`--strict-markers --strict-config`、`xfail_strict`、`filterwarnings = error`；覆盖率 branch 模式，`fail_under = 85`。

## 分层

marker 按 `tests/` 下的一级目录自动添加，无需手写。

| 目录 / marker | 依赖 | 运行 | 内容 |
|---|---|---|---|
| `unit` | 无 | `make test-unit` | 纯函数、配置、路由、OpenAPI 导出、生成器、提交信息检查等 |
| `conventions` | 无 | `make test-conventions` | 探针路由逐条断言约定 §2–§4、§6：信封、错误码登记表、校验规则、400 / 404 / 405 / 413 / 415、请求头、分页、类型序列化 |
| `integration` | Docker（testcontainers） | `make test` | 每个模块的 HTTP 行为、数据层、限流、健康检查、指标、文件、队列与定时任务 |
| `e2e` | Docker Compose | `make test-e2e` | 生产镜像完整服务栈黑盒测试 |

`make test` = unit + conventions + integration（`-n auto`，输出 `coverage.xml`）；`make check` 只跑 unit。

## 环境隔离

- 会话开始与每个用例前清除全部配置相关环境变量，写入固定的测试密钥（`RATE_LIMIT_DEFAULT` 放宽到不触发），并 `chdir` 到临时目录，工作目录下的 `.env` 不生效
- 需要生产配置的用例使用 `tests.conftest.use_production`

## 集成测试

| fixture | 说明 |
|---|---|
| `pg_server` / `redis_url` | 会话级 testcontainers：`postgres:18.6-alpine`、`redis:8.10.3-alpine` |
| `template_db` | 在模板库上执行 `alembic upgrade heads` 与 seed |
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

## e2e

`make test-e2e` 以 base + postgres + redis + ci 组合（项目名 `E2E_PROJECT`，默认 `fastapi-e2e`）`up --build --wait` 起完整服务栈，宿主端口随机分配，结束后 `down --volumes`；启动或用例失败时输出最近 200 行日志。`E2E_KEEP_STACK=1` 保留服务栈；设置 `CI` 时失败的服务栈不 down，由 workflow 收集容器日志。

覆盖：`/health/ready`、邮箱注册全流程（Mailpit 收信）、文件上传与签名地址下载、限流头与 429、`/metrics` 的 HTTP 指标按稳定语义约定、`/openapi.json` 可访问。

## 编写约定

- 用例名只陈述行为
- 新模块至少有集成测试覆盖成功路径与各类错误形状
- 依赖真实提交的场景用 `committed_app`，其余用回滚模式
