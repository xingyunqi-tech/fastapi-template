# 架构

## 目录结构

```
src/app/
├── __main__.py          # python -m app：Granian 启动（WEB_CONCURRENCY 个进程）
├── main.py              # app = create_app()，fastapi dev / Granian 入口
├── factory.py           # create_app()、build_context()、lifespan
├── cli/                 # Typer `app` 命令：migrate、seed、worker、scheduler、permissions、generate
├── config/              # Settings，按分组拆分（app、auth、database、redis、mail、file、throttler、metrics、log、observability、sms、wechat、alipay）
├── contract/schemas.py  # 由 scripts/gen_schemas.py 从基线生成的 Pydantic 模型（类名即 nestjs DTO 名），禁止手改
├── common/              # 路由、信封、异常与翻译、校验引擎、中间件、分页、安全、i18n
├── core/                # auth、users、session、permissions
├── infrastructure/      # database、cache、redis（锁）、throttler、logging、metrics、health、observability、queue、scheduler、audit、alerting、account_cleanup
├── integrations/        # mail、sms（阿里云）、wechat、alipay
└── modules/             # home、files、config、regions、notifications、addresses、favorites、feedbacks、consents、app_profile
```

仓库根的其它目录：

| 目录 | 内容 |
|---|---|
| `openapi/` | `nestjs-baseline.json`（`/docs-json` 原文）、`validation.json`（class-validator 元数据）、`nest_handlers.json`（限流键映射）、`SOURCE`（nestjs 提交号）；运行时读取，镜像中包含 |
| `db/nestjs-schema.sql` | nestjs 迁移后的表结构快照，parity 表结构比对基线 |
| `migrations/` | Alembic（async env.py） |
| `scripts/` | `openapi-sync.sh` 与 PEP 723 脚本（基线导出、schema 快照、校验用例、限流 handler 映射）、`gen_schemas.py`、`check_commit_msg.py`、`audit_severity.py` |
| `tests/` | `unit`、`integration`、`contract`、`parity`、`e2e`，见 [testing.md](testing.md) |
| `docker/`、`deploy/`、`.github/workflows/` | 见 [deploy.md](deploy.md) |

## 模块分层

| 文件 | 职责 |
|---|---|
| `models.py` | SQLAlchemy ORM（表名、列名与 nestjs 一致，camelCase 列名经 `mapped_column("firstName")` 映射到 snake_case 属性） |
| `repository.py` | 数据访问 |
| `service.py` | 业务逻辑，只依赖 repository |
| `schemas.py` | 本地请求 / 查询模型（契约模型在 `app.contract.schemas`） |
| `serializers.py` | 按视图（me / admin / public）输出字段 |
| `router.py` | 薄路由，`ApiRouter.endpoint(...)` 声明 |
| `account_cleaner.py` | 注销账号后的数据清理器（`@register_account_cleaner(order)`） |

新模块需在 `factory.create_app` 中 `include_routes`，在 `infrastructure/database/models.py` 中导入 ORM 模型。路由注册顺序与 nestjs AppModule 中 controller 顺序一致，契约测试校验相对顺序。

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
| `auth` | 必填：`PUBLIC`、`JWT`、`OPTIONAL_JWT`、`REFRESH_JWT`、`MONITORING`；未声明的路由在 `finalize_routes` 时报错 |
| `permissions` / `any_permissions` | RBAC，AND / OR；`ApiRouter(rbac=True)` 时全部路由经过权限守卫 |
| `status_code` | 默认 200；只有 `POST files/upload` 为 201 |
| `wrap` | 默认包装为信封；`/metrics` 等为 `False` |
| `throttle` | 路由级限流规则（ttl 毫秒） |
| `audit` | `Auditable(...)`，请求成功后写 `audit_logs` |

`finalize_routes` 把所有路由改为不区分大小写、尾斜杠可选（Express 语义）；方法不符返回 404 信封而非 405。

## 请求链路

中间件由外到内：

| 顺序 | 中间件 | 作用 |
|---|---|---|
| 1 | `RequestContextMiddleware` | Request ID（入站 `X-Request-Id` 合法则沿用）、`Accept-Language` 解析 |
| 2 | `CorsMiddleware` | 生产按 `FRONTEND_DOMAIN` 白名单，其它环境反射来源 |
| 3 | `RequestLoggingMiddleware` | pino-http 形状的请求日志 |
| 4 | `MetricsMiddleware` | `METRICS_ENABLED=true` 时挂载 |
| 5 | `RateLimitHeadersMiddleware` | 写 `X-RateLimit-*` / `Retry-After` |
| 6 | `SecurityHeadersMiddleware` | 与 nestjs helmet 相同的头集合 |
| 7 | `JsonBodyMiddleware` | 路由匹配前解析 JSON 体，256kb 上限（413）与解析错误（400） |
| 8 | `RawPathMiddleware` | 按原始（百分号编码）路径匹配路由 |
| 9 | `CatchAllMiddleware` | 未处理异常转 500 信封 |

路由内依赖顺序：限流守卫 → 鉴权 → 权限守卫 → 参数解析与校验（`common/params.py` 按 `openapi/validation.json` 复刻 nestjs ValidationPipe）→ handler → 信封包装。

数据库：每请求一个 `AsyncSession`（`DbSession` 依赖），见 [database.md](database.md)。

## 响应与错误

| 情况 | 输出 |
|---|---|
| 成功 | `{"code": 200, "message": "success", "data": ...}`，HTTP 200（upload 为 201）；`void` 接口无 `data` 键 |
| HTTP 异常 | `{"code": <状态>, "message": <译文>, "data": <对象或 null>}`（`common/errors.py`，翻译规则同 nestjs AllExceptionsFilter） |
| 校验失败 | 422，`data: {字段: "约束1, 约束2"}` |
| 非法 `:id` | 400，`data: {"id": "Invalid id"}` |
| 未匹配路由 / 方法不符 | 404，`message` 为 `Cannot <METHOD> <url>` |

翻译文件：`common/i18n/{en,zh}/common.json`（与 nestjs 相同），邮件模板文案在同目录其它 JSON 中。

## 进程模型

| 进程 | 命令 | 说明 |
|---|---|---|
| api | `python -m app` | Granian ASGI，`WEB_CONCURRENCY` 个 worker 进程 |
| worker | `app worker` | Taskiq worker，消费 Redis Stream；需 `REDIS_ENABLED=true`，Redis 不可达时退出 |
| scheduler | `app scheduler` | Taskiq scheduler，按 cron 投递定时任务 |
| migrate | `app migrate && app permissions clear-cache [&& app seed]` | compose 中的一次性服务，api 依赖其成功完成 |

`REDIS_ENABLED=false` 时队列任务在 API 进程内执行（仅开发 / 测试）。

## 基础设施

| 能力 | 位置 | 要点 |
|---|---|---|
| 缓存 | `infrastructure/cache` | 键 `cache::<key>`、值 `{"value","expires"}`（与 Keyv 互通）；未启用 Redis 时用进程内存 |
| 锁 | `infrastructure/redis` | `lock:<key>`，Redis 不可用时退回进程内锁 |
| 限流 | `infrastructure/throttler` | GCRA（Redis Lua），键 `throttle:<sha256>:default:gcra`；JWT 路由按用户、其余按 IP（`TRUST_PROXY` 跳数）；Redis 故障回落内存并告警 |
| 队列 | `infrastructure/queue` | Taskiq + Redis Stream：`taskiq:mail`、`taskiq:notification`、`taskiq:account-cleanup`、`taskiq:scheduler` |
| 定时任务 | `infrastructure/scheduler` | 03:00 UTC 清理软删会话、04:00 UTC 清理已读通知、每小时 25 分（Asia/Shanghai）账号清理补偿；执行时持有 `lock:scheduler:<name>` |
| 日志 | `infrastructure/logging` | structlog；生产 JSON、开发彩色单行；带 request_id、trace_id / span_id、user_id，敏感字段脱敏 |
| 指标 | `infrastructure/metrics` | `/metrics`（`METRICS_PATH`），指标名、标签、桶同 nestjs；多进程共享 `PROMETHEUS_MULTIPROC_DIR` |
| 健康检查 | `infrastructure/health` | `/api/v1/health`（监控令牌）、`/health/live`、`/health/ready`、`/api/health` |
| 链路 / 错误上报 | `infrastructure/observability` | `OTEL_ENABLED`（OTLP/HTTP）、`SENTRY_DSN`，默认关闭 |
| 审计 | `infrastructure/audit` | `audit=Auditable(...)` 的路由成功后写 `audit_logs` |
| 告警 | `infrastructure/alerting` | 写 `admin_alert`，按 dedupKey 去重并通知管理员 |
| 账号清理 | `infrastructure/account_cleanup` | 注销后入队，按 order 执行各模块清理器；`POST admin/users/:id/cleanup` 重新触发 |
| 邮件 | `integrations/mail`、`queue/mail.py` | 设置 `MAIL_HOST` 时提交后入 mail 队列，否则只记日志 |
| 文件 | `modules/files` | `FILE_DRIVER`：`local`（`./files`）、`s3`、`s3-presigned` |

## 配置

`app.config.get_settings()` 一次性加载全部分组并汇总所有错误后失败。来源：进程环境变量 + 工作目录 `.env`（空值视为未设置），变量名与 nestjs `env-example-relational` 一致，全部变量见 `.env.example`。生产环境额外校验：四个 JWT 密钥 ≥32 字符且互不相同、`REDIS_ENABLED=true`、`MAIL_HOST` / `MAIL_DEFAULT_EMAIL` / `MAIL_DEFAULT_NAME` 必填。

与 nestjs 的行为差异见 [contract.md](contract.md) 的「与 nestjs 的行为差异」一节。
