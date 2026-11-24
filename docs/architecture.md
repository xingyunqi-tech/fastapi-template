# 架构

## 目录结构

```
src/app/
├── __main__.py          # python -m app：Granian 启动（WEB_CONCURRENCY 个进程）
├── main.py              # app = create_app()，fastapi dev / Granian 入口
├── factory.py           # create_app()、build_context()、lifespan
├── cli/                 # Typer `app` 命令：migrate、seed、worker、scheduler、keys、users、generate、openapi
├── config/              # Settings，按分组拆分（app、auth、database、redis、mail、storage、rate_limit、log、telemetry、events、sms、wechat、alipay）
├── common/              # 路由、信封、错误码、校验转换、类型、中间件、分页、幂等键、i18n
├── security/            # 密钥与 kid、JWT、JWKS（验证方）、吊销键、角色与权限、密码哈希、鉴权依赖
├── identity/            # 签发方：auth、sessions、me、users、providers（短信、微信、支付宝）、account_deletion、keys（JWKS）
├── infrastructure/      # database、cache、redis（键前缀、锁）、throttler、logging、telemetry、health、queue、scheduler、audit、events、mail、account_data（注销清理器登记）
└── modules/             # files、notes（CRUD 示例）
```

仓库根的其它目录：

| 目录 | 内容 |
|---|---|
| `openapi/` | `openapi.json`（`app openapi export` 由代码导出，`--check` 校验一致） |
| `migrations/` | Alembic（async env.py） |
| `scripts/` | `check_commit_msg.py`、`audit_severity.py` |
| `tests/` | `unit`、`conventions`、`integration`、`e2e`，见 [testing.md](testing.md) |
| `docker/`、`deploy/`、`.github/workflows/` | 见 [deploy.md](deploy.md) |

## 模块分层

| 文件 | 职责 |
|---|---|
| `models.py` | SQLAlchemy ORM：snake_case 列名、UUIDv7 主键、`timestamptz`，声明 `__branch__` |
| `repository.py` | 数据访问 |
| `service.py` | 业务逻辑，只依赖 repository |
| `schemas.py` | 本地请求 / 查询模型（契约模型在 `app.contract.schemas`） |
| `serializers.py` | 按视图（me / admin / public）输出字段 |
| `router.py` | 薄路由，`ApiRouter.endpoint(...)` 声明 |
| `account_cleaner.py` | 注销账号后的数据清理器（`@register_account_cleaner(order)`） |

新模块需在 `factory.mount_routes` 中 `include_routes`，在 `infrastructure/database/models.py` 中导入 ORM 模型。

## 签发方与验证方

设置 `AUTH_SIGNING_KEY` 为签发方，设置 `AUTH_JWKS_URL` 为验证方，二者只能设置一个。`factory.mount_routes` 按角色与已配置的依赖挂载路由：

| 条件 | 挂载 |
|---|---|
| 签发方 | `identity/*`、`/.well-known/jwks.json`；JWT 路由另更新会话活跃时间 |
| 配置 `DATABASE_URL` | `audit`、`notes` |
| 配置 `DATABASE_URL` 与 `STORAGE_DRIVER` | `files` |
| 始终 | `/health/*`，`OTEL_METRICS_EXPORTER=prometheus` 时 `/metrics` |

签发方必须配置 `DATABASE_URL` 与 `STORAGE_DRIVER`。验证方的 access 令牌用 `security/jwks.py` 从 `AUTH_JWKS_URL` 取到的公钥校验，吊销键与签发方共用 Redis；验证方的 `app scheduler` 不调度签发方的清理任务。

## 路由声明

所有接口通过 `ApiRouter.endpoint()` 注册为 `ApiRoute`：

```python
@router.endpoint(
    "POST", "", operation_id="createAddress", auth=Auth.JWT,
    response_model=Address, permissions=[...], throttle=Throttle(5, 60_000),
)
```

| 参数 | 说明 |
|---|---|
| `operation_id` | 与基线一致 |
| `auth` | 必填：`PUBLIC`、`JWT`、`OPTIONAL_JWT`、`MONITORING`；未声明的路由在 `finalize_routes` 时报错 |
| `permissions` / `any_permissions` | RBAC，AND / OR；`ApiRouter(rbac=True)` 时全部路由经过权限守卫 |
| `status_code` | 默认 200；只有 `POST files/upload` 为 201 |
| `wrap` | 默认包装为信封；`/metrics` 等为 `False` |
| `throttle` | 路由级限流规则（ttl 毫秒） |
| `audit` | `Audit(action, resource=...)`，请求成功后写 `audit_logs` |

`finalize_routes` 把所有路由改为不区分大小写、尾斜杠可选（Express 语义）；方法不符返回 404 信封而非 405。

## 请求链路

中间件由外到内：

| 顺序 | 中间件 | 作用 |
|---|---|---|
| 1 | `CatchAllMiddleware` | 未处理异常转 500 `common.internalError` |
| 2 | `SecurityHeadersMiddleware` | 安全响应头 |
| 3 | `RateLimitHeadersMiddleware` | 写 `X-RateLimit-*` / `Retry-After` |
| 4 | `RequestLoggingMiddleware` | 请求日志 |
| 5 | `CORSMiddleware` | 生产按 `CORS_ORIGINS` 白名单，其它环境允许任意来源；不发送 credentials |
| 6 | `RequestContextMiddleware` | Request ID（入站 `X-Request-Id` 合法则沿用，否则 UUIDv7）、`Accept-Language` 解析 |

路由内顺序：`BODY_LIMIT` 计数（上传路由除外）→ 限流守卫 → 鉴权 → 权限守卫 → 幂等键 → Pydantic 解析与校验 → handler → 信封包装。

数据库：每请求一个 `AsyncSession`（`DbSession` 依赖），见 [database.md](database.md)。

## 响应与错误

| 情况 | 输出 |
|---|---|
| 成功 | `{"code": 0, "message": "ok", "data": ...}`；无返回值时 `data: null` |
| 错误 | `{"code", "message", "errorKey", "data", "requestId"}`；`raise AppError("<errorKey>")`，登记表见 `common/errors.py`（与约定 §2.4 一致） |
| 校验失败 | 422 `common.validationFailed`，`data.errors[{field, rule, message, params?}]`；业务校验用 `field_error(field, rule)` |
| 未匹配路由 / 方法不符 | 404 `common.notFound` / 405 `common.methodNotAllowed`（带 `Allow`） |

翻译文件：`common/i18n/{en,zh}/errors.json`（errorKey）、`rules.json`（校验 rule），邮件文案在 `common.json` 等文件中。

## 进程模型

| 进程 | 命令 | 说明 |
|---|---|---|
| api | `python -m app` | Granian ASGI，`WEB_CONCURRENCY` 个 worker 进程 |
| worker | `app worker` | Taskiq worker，消费 Redis Stream `{OTEL_SERVICE_NAME}:tasks`；需 `REDIS_URL`，Redis 不可达时退出 |
| scheduler | `app scheduler` | Taskiq scheduler，按 cron 投递定时任务 |
| migrate | `app migrate [&& app seed]` | compose 中的一次性服务，api 依赖其成功完成 |

未设置 `REDIS_URL` 时队列任务在 API 进程内执行（仅开发 / 测试）。

## 基础设施

| 能力 | 位置 | 要点 |
|---|---|---|
| 缓存 | `infrastructure/cache` | 键 `{OTEL_SERVICE_NAME}:cache:<key>`、值 `{"value","expires"}`；未设置 `REDIS_URL` 时用进程内存 |
| 锁 | `infrastructure/redis` | `{OTEL_SERVICE_NAME}:lock:<key>`，Redis 不可用时退回进程内锁；`key()` 生成约定 §10 的服务键前缀 |
| 限流 | `infrastructure/throttler` | 约定 §7 的 GCRA（Redis Lua，键 `rl:{规则}:{主体}`）；路由 `rate_limit=` 声明共享规则，未声明用私有默认规则；客户端 IP 按 `TRUSTED_PROXIES`；Redis 出错时共享规则 503，私有规则放行并计入 `ratelimit.fallback` |
| 队列 | `infrastructure/queue` | Taskiq + Redis Stream `{OTEL_SERVICE_NAME}:tasks`（消费组同服务名） |
| 定时任务 | `infrastructure/scheduler` | 仅签发方：03:00 UTC 删除已过期或吊销超过 7 天的会话与过期的一次性令牌、每小时 25 分（Asia/Shanghai）账号清理补偿；执行时持有 `{OTEL_SERVICE_NAME}:lock:cron:<name>` |
| 日志 | `infrastructure/logging` | structlog；生产 JSON、开发彩色单行；带 request_id、trace_id / span_id、user_id，敏感字段脱敏 |
| 指标与链路 | `infrastructure/telemetry` | OpenTelemetry；HTTP 指标按稳定语义约定（`http.server.request.duration`）；`OTEL_METRICS_EXPORTER=prometheus` 时 API 挂 `/metrics`、worker 监听 `OTEL_EXPORTER_PROMETHEUS_PORT`，此时 `WEB_CONCURRENCY` 须为 1；`otlp` 时推送；配置 OTLP 端点时开启链路；`SENTRY_DSN` 可选，不采集局部变量；出站 httpx span 与 Sentry 面包屑的 URL 去掉查询串 |
| 健康检查 | `infrastructure/health` | `/health/live` 恒 200；`/health/ready` 检查数据库与已配置的 Redis，失败 503 |
| 审计 | `infrastructure/audit` | `audit=Audit(...)` 的路由成功后写 `audit_logs`（请求体脱敏）；`GET /api/v1/audit-logs`（`audit:read`） |
| 账号清理 | `identity/account_deletion`、`infrastructure/account_data` | 签发方注销提交后入队清理任务：先发布 `user.deleted`（`infrastructure/events`，`EVENTS_TRANSPORT`），成功后记 `account_deletions.event_published_at`，再按 order 执行清理器（sessions、files、notes）；发布或清理失败记 `last_error` 并重试，最终失败记 error 日志，定时补偿重新入队。配置了数据库且 `EVENTS_TRANSPORT=redis_stream` 的验证方由 `app worker` 消费 `user.deleted`（消费组为 `OTEL_SERVICE_NAME`），执行本服务的清理器；失败的消息空闲 60 秒后重领，投递满 5 次记 error 后确认；消费组缺失时从 stream 开头重建（清理器幂等） |
| 邮件 | `infrastructure/mail`、`queue/mail.py` | 模板 `verify-email`、`reset-password`（zh / en），链接为 `APP_FRONTEND_URL` + 路径 + `?token=`；设置 `MAIL_HOST` 时提交后入 mail 队列，否则只记日志 |
| 文件 | `modules/files` | 约定 §11.5；`STORAGE_DRIVER`：`local`（`STORAGE_LOCAL_DIR`，签名地址由本服务输出）、`s3`（预签名地址）；类型按魔数识别，大小按用途上限 |

## 配置

`app.config.get_settings()` 一次性加载全部分组并汇总所有错误后失败。来源：进程环境变量 + 工作目录 `.env`（空值视为未设置），公共变量见约定 §9，全部变量见 `.env.example`。`AUTH_ISSUER`、`AUTH_AUDIENCE`、`OTEL_SERVICE_NAME` 始终必填，`AUTH_SIGNING_KEY` 与 `AUTH_JWKS_URL` 恰好设置一个；签发方另要求 `DATABASE_URL`、`STORAGE_DRIVER`；设置 `STORAGE_DRIVER` 时要求 `DATABASE_URL`，验证方使用本地存储时要求 `STORAGE_URL_SECRET`。生产环境另要求 `REDIS_URL`、`CORS_ORIGINS`，签发方要求 `APP_FRONTEND_URL`、`MAIL_HOST`、`MAIL_FROM`，Prometheus 拉取时要求 `METRICS_TOKEN`，本地存储时要求 `STORAGE_URL_SECRET`。
