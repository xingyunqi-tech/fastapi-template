# fastapi-temp 方案 v2

> 编写日期：2026-11-17。版本数据来自当日 PyPI / endoflife.date 查询，见文末。
>
> 对外行为以 [docs/platform-conventions.md](docs/platform-conventions.md)（下称「约定」）为准。凡涉及路径、信封、错误码、校验、分页、数据类型、令牌、密码、角色权限、请求头、限流、日志、公共环境变量、monorepo 共存、身份与文件接口的地方，本文只写「见约定 §x」和本服务的实现落点，不重复约定内容。

---

## 1. 目标

1. 独立的 Python 后端模板：Python / FastAPI 当前最新稳定技术栈与惯用写法。
2. 不绑定任何前端模板的业务；遵守约定，code-first 导出本仓库自己的 OpenAPI 文档供前端生成客户端。
3. 只提供基础功能（§3），外加一个 CRUD 示例模块和模块生成器。
4. 可单独部署，也可作为 monorepo 中的一个服务（约定 §10），按配置成为令牌**签发方**或**验证方**。

## 2. 现状（v1 已实现）

| 项 | 状态 |
|---|---|
| 进度 | M0–M5 完成并已 push：FastAPI + SQLAlchemy 2.1 async + Alembic + Taskiq + Granian，Docker / compose / manage.sh / 4 个 workflow |
| 契约 | 以 nestjs-template 为唯一来源：`/docs-json` 输出入库的 nestjs 基线原文；`common/validation/` 复刻 class-validator（含 validator.js 移植）；`contract/schemas.py` 由基线生成；Express 路由语义；成功一律 HTTP 200；`{code, message, data}` 信封无 errorKey |
| 数据 | 单个初始迁移，表结构照搬 nestjs：camelCase 列、int 主键、`timestamp` 无时区 |
| 认证 | HS256 JWT（access + refresh 均为 JWT）、客户端发 MD5、argon2id / bcrypt 双哈希、会话表、RBAC（int 角色 id，存库） |
| 模块 | core（auth / users / session / permissions）+ files + home + 8 个通用业务模块（config、regions、notifications、addresses、favorites、feedbacks、consents、app_profile） |
| 指标 | prometheus-client multiprocess 模式（`PROMETHEUS_MULTIPROC_DIR`），指标名照搬 nestjs |
| 测试 | unit / integration / contract（OpenAPI 与 validation.json 比对、4516 条 oracle 用例）/ parity（与 nestjs 同请求比对）/ e2e |
| 共享产物 | 本仓库是 `openapi/*`、`db/nestjs-schema.sql`、`validation-cases.json`、`scripts/openapi-sync.sh` 的生成方，go-template / springboot-template 复制 |
| 规模 | `src/app` 约 17.3k 行，`tests` 约 12.1k 行 |

## 3. 范围

### 3.1 实现

| 类别 | 内容 |
|---|---|
| 平台 | 配置、日志、错误信封、i18n（zh / en）、Request ID、CORS、安全头、限流、幂等键、健康检查、指标、OTel、Sentry、OpenAPI 导出 |
| 身份（签发方） | 约定 §11.1–§11.3：邮箱 + 密码、手机号 + 短信验证码、JWT（EdDSA）+ JWKS、refresh 轮换、会话管理、绑定手机号、用户管理、角色分配 |
| 可选 provider | 约定 §11.1 的 `wechat_miniapp`、`wechat_app`、`alipay` 登录与 §11.2 的微信手机号绑定；默认关闭，配置凭据后启用 |
| 文件 | 约定 §11.5；驱动 local / s3 |
| 基础设施 | 邮件（SMTP + Jinja2）、队列（Taskiq）、定时任务、审计日志、缓存与分布式锁 |
| 账号注销清理 | **保留**：应用商店要求可注销账号，注销后异步清理用户数据是通用需求；并按约定 §10 发布 `user.deleted` 事件 |
| 示例与工具 | `notes` CRUD 示例模块、`app generate resource` 模块生成器、seed、CLI |
| monorepo | 签发 / 验证两种角色、独占 schema、`user.deleted` 事件的发布与消费 |

### 3.2 不做

| 内容 | 理由 |
|---|---|
| regions、addresses、favorites、feedbacks、consents、app_profile、notifications | 业务模块，用户已决定删除 |
| config 模块 | 配置项全部是考试业务（`ai.*`、`practice.*`、`paper.*`、`wrongBook.*`、`consent.*`、`agreement.*`、`app.profile`） |
| 告警（`admin_alert` + 站内通知） | 依赖 notifications；运维告警由 Sentry 与 Prometheus 告警规则承担 |
| `home`（`GET /`、`GET /api/health`） | 由约定 §1 的 `/health/*` 替代 |
| nestjs 契约体系 | 基线、validation.json、class-validator 复刻、parity、nestjs-drift、共享产物生成 |
| 对象存储直传（presign） | 约定 §11.5 列为各服务扩展，本期不做 |
| Connect / gRPC | 约定 §10 为建议；Python 侧 connectrpc 仍为 0.x 且依赖 protobuf-py / pyqwest 而非 Google protobuf，等 monorepo 实际需要时再引入 |

## 4. 技术栈

| 类别 | 选型 | 版本 | 说明 |
|---|---|---|---|
| 语言 | Python | 3.15.2 | 保留；`uuid.uuid7()`、PEP 810 `lazy import` 已在用 |
| 包管理 | uv | 0.13.9 | 保留 |
| Web | FastAPI / Starlette | 0.163.2 / 1.10.0 | 保留；改为 code-first OpenAPI 3.1 |
| 校验 | Pydantic | 2.14.3 | 保留；**改用原生校验**，错误按约定 §2.3 转换，删除 class-validator 复刻 |
| 配置 | pydantic-settings | 2.16.3 | 保留 |
| ORM / 驱动 / 迁移 | SQLAlchemy async / asyncpg / Alembic | 2.1.9 / 0.32.0 / 1.20.1 | 保留 |
| 数据库 | PostgreSQL | 18.7 | **只用 18**：原生 `uuidv7()` 作列默认值 |
| Redis | Redis / redis-py | 8.10.3 / 8.1.0 | 保留 |
| 队列 + 定时 | Taskiq / taskiq-redis | 0.13.0 / 1.2.4 | 保留 |
| JWT | PyJWT[crypto] | 2.15.1 | 保留；**算法改 EdDSA**；JWKS 用 httpx 异步拉取后交给 PyJWT 校验，不用同步的 `PyJWKClient` |
| 加密 | cryptography | 50.0.2 | 保留；Ed25519、RFC 7638 指纹、宽限期令牌对的 AES-GCM + HKDF |
| 密码哈希 | pwdlib[argon2] | 0.4.2 | 升级；**去掉 bcrypt**；`Argon2Hasher` 显式传入约定 §5.4 的参数 |
| 日志 | structlog | 26.1.0 | 保留；字段与脱敏按约定 §8 |
| 指标 | OpenTelemetry SDK + exporter-prometheus | 1.45.2 / 0.66b2 | **改为 OTel 指标**：指标名走稳定语义约定；prometheus-client 只作为 exporter 的间接依赖，不使用其 multiprocess 模式 |
| 链路 | OTel instrumentation（fastapi / sqlalchemy / redis / httpx）+ OTLP HTTP | 0.66b2 / 1.45.2 | 保留；只读标准 `OTEL_*` 变量 |
| 错误上报 | sentry-sdk[fastapi] | 2.75.0 | 保留 |
| 服务器 | Granian | 2.8.8 | 保留 |
| JSON | orjson | 3.13.0 | 保留，用于日志渲染；响应由 Pydantic 直接序列化 |
| HTTP 客户端 | httpx | 0.28.1 | 保留（1.0 仍未发布） |
| 对象存储 / 邮件 | aioboto3 / aiosmtplib + Jinja2 | 15.5.0 / 5.1.3 + 3.1.6 | 保留 |
| 上传 | python-multipart | 0.0.34 | 保留 |
| CLI | Typer | 0.29.1 | 保留 |
| 事件 | cloudevents | 2.2.0 | **新增**：约定 §10 的 `user.deleted` 事件 |
| 文档 UI | Scalar（CDN 固定版本） | — | 保留现有静态页，`data-url` 改指 `/openapi.json` |
| 本地开发服务器 | fastapi-cli | 0.0.34 | 保留；uvicorn 由它间接引入，不列为直接依赖 |
| 测试 | pytest / pytest-asyncio / pytest-xdist / coverage | 9.2.0 / 1.5.0 / 3.8.0 / 7.17.3 | 保留 |
| 测试辅助 | testcontainers / time-machine / httpx2 | 4.15.0 / 3.6.0 / 2.16.2 | 保留 |
| 测试数据 | polyfactory | 3.5.0 | **新增**：按 Pydantic / ORM 模型生成测试数据 |
| API 模糊测试 | schemathesis | 4.33.4 | **新增**：按导出的 OpenAPI 生成请求，校验响应符合文档与约定 |
| OpenAPI 兼容性 | oasdiff（Docker 镜像） | 固定 tag | **新增**：PR 中报告对 main 的破坏性变更 |
| Lint / 类型 | Ruff / mypy strict / ty | 0.17.5 / 2.4.1 / 0.0.105 | 保留；ty 继续试运行 |
| 钩子 / 拼写 | prek / typos | 0.5.11 / 1.52.2 | 保留 |
| 删除 | psutil、bcrypt extra、uvicorn（直接依赖）、aiosmtpd（dev，改用 Mailpit 容器） | — | 无对应功能或已有替代 |

不选：Litestar（生态与团队熟悉度不及 FastAPI）、fastapi-users（令牌与会话模型由约定决定，需全部定制）、SQLModel（仍为 0.0.x，与 SQLAlchemy 2.1 async 用法重叠）、Celery / arq（非原生 async / 停更）。

## 5. 目录结构与分层

```
src/app/
├── main.py  factory.py  __main__.py   # create_app()、lifespan、Granian 入口
├── config/                            # Settings 分组（§10）
├── common/
│   ├── envelope.py                    # ApiResponse[T]、ErrorResponse、ValidationErrorResponse
│   ├── errors.py                      # AppError(error_key)、约定 §2.4 登记表
│   ├── validation.py                  # Pydantic 错误 → 约定 §2.3
│   ├── handlers.py                    # 全局异常处理
│   ├── middleware.py                  # Request ID、语言、body 上限、安全头、CORS、访问日志
│   ├── routing.py                     # ApiRouter.endpoint：operationId、auth、status、permissions、rate_limit、audit、idempotent、upload
│   ├── pagination.py                  # PageParams / CursorParams / SortParam
│   ├── types.py                       # UtcDatetime、Email、Phone、UUIDv7、Money
│   ├── openapi.py  idempotency.py  i18n/{zh,en}/  docs.py  context.py
├── security/                          # 签发方与验证方共用：JWT 校验、JWKS 缓存、吊销检查、Principal、RBAC（代码固定映射）
├── identity/                          # 仅签发方加载
│   ├── auth/                          # 约定 §11.1
│   ├── me/  users/  sessions/         # 约定 §11.2、§11.3
│   ├── providers/{sms,wechat,alipay}/ # provider 协议与实现
│   ├── account_deletion/  keys/       # 注销清理；签名密钥与 JWKS 端点
├── infrastructure/
│   ├── database/  redis/（客户端、锁）  cache/  throttler/  logging/  telemetry/（OTel、指标、Sentry）
│   ├── health/  queue/  scheduler/  audit/  mail/  storage/  events/（CloudEvents 发布 / 消费）
├── modules/
│   ├── files/                         # 约定 §11.5
│   └── notes/                         # CRUD 示例
└── cli/                               # migrate、seed、worker、scheduler、generate、openapi export、keys generate、users cleanup
migrations/versions/{platform,identity,modules}/   # Alembic 多分支，见 §8
openapi/openapi.json                   # 导出产物，入库
```

模块内分层：`router.py`（薄）→ `service.py`（业务、事务边界）→ `repository.py`（SQLAlchemy 查询）；`models.py`（ORM）、`schemas.py`（Pydantic 请求 / 响应，camelCase 别名）。不设 domain / mapper 层。每请求一个 `AsyncSession`，`after_commit` 执行提交后的副作用（发邮件、入队、发事件）。

## 6. 约定落地

| 约定 | 落地 |
|---|---|
| §1 路径与文档 | `/api/v1` 业务前缀；`/health/*`、`/metrics`、`/.well-known/jwks.json`、`/openapi.json`、`/docs` 不带前缀；`app openapi export` 写 `openapi/openapi.json`；`ApiRouter.endpoint` 要求显式 `operation_id` 与 `auth`，启动时校验 operationId 全局唯一、lowerCamelCase、动词开头；去掉 FastAPI 默认的 `HTTPValidationError`，错误响应引用 `ErrorResponse`，422 引用 `ValidationErrorResponse` |
| §1 路由 | `redirect_slashes=False`；Starlette 的 405 包成 `common.methodNotAllowed` 信封并保留 `Allow` 头 |
| §2.1 成功 | 响应模型统一 `ApiResponse[T]`；状态码由 `endpoint(status=...)` 按约定 §11 逐接口声明（如 `registerWithEmail` 201、`deleteMe` / `deleteUser` 202）；无返回值时 `data: null` |
| §2.2 / §2.4 错误 | `AppError(error_key)` 查登记表得到状态与 `code`，登记表只收录约定 §2.4 的条目，单元测试逐条比对；`message` 用 `i18n/<lang>/errors.json` 按 `errorKey` 翻译；401 按约定写 `WWW-Authenticate`；未捕获异常 500 `common.internalError`，响应不含异常信息，上报 Sentry |
| §2.3 校验 | Pydantic 每个失败字段只取第一条错误（约定允许）。类型映射见下表；`loc` 转 `field`（body 去前缀，query / path 加前缀；整个请求体缺失时 `loc` 为 `('body',)`，输出 `field: "body"`、`rule: required`）；字符串长度为 Python `len`，即按 Unicode 码点计数；`json_invalid`、非 UTF-8 与 `NaN` / `Infinity` → 400 `common.malformedJson`（路由内先按 UTF-8 + 严格 JSON 解析一次请求体）；非 JSON / multipart 的 `Content-Type` → 415；`extra="ignore"`；`ApiModel` 对 `int` / `float` / `bool` 不做类型转换（`"18"`、`true` 传给 int 为 `type`），查询参数模型关闭；联合类型的分支标签从 `loc` 中去掉；`gt` / `lt` 对整数折算为 `min` / `max` 参数；时间输入须带时区偏移；数字只认 ASCII；路径整数 id 用 `SerialId`（1–2³¹−1）；方法不符时 405 先于 413；body 上限由 `ApiRoute.handle` 包装 `receive` 按 `BODY_LIMIT` 计数（先比对 `Content-Length`），超出 413 `common.payloadTooLarge`，`endpoint(upload=True)` 的路由豁免（见 §9「文件」） |
| §3 分页 | `PageParams`、`CursorParams`（游标为 base64url 编码的 `(sort_key, id)`）、`sort_param(allowed, default)` 依赖工厂，用法 `Annotated[list[SortField], Depends(sort_param(...))]`；非法 `sort` / `cursor` 抛 `RequestValidationError`，`field` 为 `query.sort` / `query.cursor`；`Page[T]`、`CursorPage[T]` 响应模型 |
| §4 数据类型 | 主键 `uuid.uuid7()`（应用侧）+ `DEFAULT uuidv7()`（库侧）；`UtcDatetime` 输出毫秒 `Z`、输入接受任意偏移；列一律 `timestamptz`；`Email` 类型去空白转小写；`Phone` 类型规范化为 E.164；枚举 `StrEnum`；金额 `int` |
| §5 认证 | 见 §7 |
| §6 请求头 | Request ID、`Accept-Language`、CORS（不发 `Allow-Credentials`）在 `common/middleware.py`；`endpoint(idempotent=True)` 开启幂等键：作用域与行为按约定 §6，Redis 键 `{service}:idem:{scope_hash}`，值含请求体 SHA-256、状态码与响应体，处理中占位用 `SET NX`，占位 TTL 5 分钟，请求被取消时删除；只保存 2xx / 4xx，5xx 删除占位；回放时响应体 `requestId` 改为本次请求的值（约定 §2.2 优先），其余字节与首次相同 |
| §7 限流 | 见 §9「限流」 |
| §8 日志与可观测性 | 见 §9「日志」「指标」 |
| §9 环境变量 | 见 §10 |
| §10 monorepo | 见 §7.5 |
| §11 身份与文件接口 | 见 §7、§9「文件」 |

Pydantic 错误类型 → `rule`（按顺序匹配，先匹配到的生效）：

| Pydantic `type` | `rule` |
|---|---|
| `missing` | `required` |
| `uuid_type`、`uuid_parsing`、`uuid_version` | `uuid` |
| `url_type`、`url_parsing`、`url_scheme`、`url_too_long` | `url` |
| `email`、`phone`（`Email` / `Phone` 类型抛出的 `PydanticCustomError`） | `email`、`phone` |
| `enum`、`literal_error` | `enum` |
| `too_short`（list / set） | `minItems` |
| `too_long`（list / set） | `maxItems` |
| `string_too_short` / `string_too_long` | `minLength` / `maxLength` |
| `greater_than(_equal)` / `less_than(_equal)` | `min` / `max` |
| `string_pattern_mismatch` | `pattern` |
| 其余 `*_type`、`*_parsing`，以及 `int_from_float` | `type` |
| `PydanticCustomError("exists", ...)` 及其他自定义类型 | 类型名原样（lowerCamelCase） |
| 未匹配 | `format`，并记 warn 日志 |

`exists`：引用其他资源的字段（`setUserRoles.roles`、`updateMe.avatarFileId` 非本人或用途不是 `avatar`）在 service 层校验，失败时抛出与 Pydantic 错误同形的 `ValidationFailed([...])`。`setUserRoles` 不含 `user` 时报 `field: roles`、`rule: contains`（约定 §2.3）。

## 7. 认证与权限

### 7.1 身份接口（签发方）

接口、字段、对象、错误码见约定 §11、§2.4；本节只写实现落点。

| 约定 | 包 | 实现要点 |
|---|---|---|
| §11.1 邮箱 | `identity/auth/` | `registerWithEmail` 提交后入队验证邮件，邮件链接取 `APP_FRONTEND_URL`；`loginWithEmail` 对不存在的账号校验固定假哈希（约定 §5.4）；`auth-login-account` 主体为 `a:{SHA-256(规范化邮箱) 前 16 位}`，只在失败时计数、成功后删除该键；`forgotPassword` 同时套 `auth-mail`（IP）与 `auth-mail-account`（账号） |
| §11.1 一次性令牌 | `identity/auth/tokens.py` | 表 `verification_tokens`，（user_id, purpose）唯一，新令牌覆盖旧令牌；只存 SHA-256；找回密码令牌另存当前密码哈希指纹，改密后失效 |
| §11.1 短信 | `identity/auth/` + `identity/providers/sms/` | 验证码、冷却、每日次数、失败计数与锁定按约定 §11.1，存 Redis `{service}:sms:*`；`loginWithSms` 套 `auth-login`（IP）与 `auth-login-account`（`a:{SHA-256(E.164) 前 16 位}`，只计失败）；短信服务调用失败返回 `common.badGateway`；`scene=bind_phone` 由路由内的可选 JWT 依赖校验；`SMS_PROVIDER=log|aliyun`，非生产默认 `log` |
| §11.1 第三方 | `identity/providers/{wechat,alipay}/` | `ProviderClient` 协议：`exchange(code) -> ProviderIdentity(openid, union_id)`；`code` 无效映射为 `auth.providerCodeInvalid`，第三方调用失败映射为 `common.badGateway`；微信按 `union_id` 或 `(provider, openid)` 任一命中视为同一身份，命中后回填缺失的 `union_id`；`registry` 收集已启用 provider，供 `listAuthProviders` 与 `loginWithProvider` 使用；路由始终注册，OpenAPI 与配置无关 |
| §11.2 当前用户 | `identity/me/` | `deleteMe` 与无密码时的 `changePassword` 用 `require_recent_login` 依赖（按 access 的 `auth_time`，约定 §5.4）；`changePassword` 校验 `currentPassword` 失败时计入 `auth-password-verify`（`u:{sub}`），成功后清除；`deleteMe` 同样执行 `role.lastAdmin` 检查；`bindPhoneWithWechat` 复用 `wechat_miniapp` 客户端 |
| §11.2 会话 | `identity/sessions/` | `current` 由 access 的 `sid` 判定；`last_active_at` 每会话最多 5 分钟写一次 |
| §11.3 管理 | `identity/users/` | `listUsers` 用 §6 的分页与排序；`role.lastAdmin`：禁用、删除、`deleteMe`、`setUserRoles` 在同一事务内先 `SELECT … FOR UPDATE` 锁住全部 active admin 的 `user_roles` 行，再计数并修改 |
| §5.1 JWKS | `identity/keys/` | `/.well-known/jwks.json` 不进 OpenAPI；JWK 字段、`kid` 计算与缓存头按约定 §5.1 |

### 7.2 令牌

- 形式、claims（含 `email_verified`、`auth_time`）、密钥、JWKS、验证规则见约定 §5.1；`app keys generate` 生成 Ed25519 密钥对；刷新时 `auth_time` 原样沿用，`email_verified` 与 `roles` 按当前数据库状态重新取值
- 验证（`security/jwt.py`，签发方与验证方共用）：签发方直接使用本地公钥集合；验证方用 httpx 异步拉取 `AUTH_JWKS_URL`，按 `Cache-Control` 缓存，未知 `kid` 触发刷新，两次刷新间隔不少于 30 秒，并发请求共用同一次刷新；先解析头部要求 `kid` 存在，再按 `kid` 取 `kty: OKP`、`crv: Ed25519` 的 JWK；PyJWT `decode` 固定 `algorithms=["EdDSA"]`、`leeway=30`。RFC 9864 的 `Ed25519` 算法名在约定切换前不接受
- 吊销：写入时机与 TTL 按约定 §5.1，`security/revocation.py` 统一写入与查询；每个受保护请求验证 JWT 后查询该键，Redis 出错时返回 503 `common.serviceUnavailable`
- 会话有效期：`sessions.expires_at` 在登录时设为登录时间 + `AUTH_REFRESH_TTL`，轮换不修改（约定 §5.2）
- 刷新（约定 §5.2）：表 `refresh_tokens` 保存会话的全部 refresh 哈希直到会话过期。轮换在一个事务内完成：`SELECT … FOR UPDATE` 锁会话行 → 查哈希 → 当前令牌则插入新令牌、把旧行标记 `rotated_at` 并写入 `grace_ciphertext`（新令牌对的 AES-256-GCM 密文，密钥为 HKDF-SHA256(旧 refresh 明文)）；已轮换且在 30 秒内则解密返回同一对令牌；已轮换且超过 30 秒则吊销会话并返回 `auth.refreshTokenReused`；查不到返回 `auth.refreshTokenInvalid`。并发刷新被行锁串行化，只产生一对新令牌，且不依赖 Redis
- `auth-refresh` 限流在路由依赖中先按 refresh 哈希查到会话，以 `s:{sid}` 计数，查不到时以 IP 计数（约定 §11.6）

### 7.3 密码

见约定 §5.4。`Argon2Hasher(memory_cost=19456, time_cost=2, parallelism=1)` 显式传参；`PASSWORD_HASH_CONCURRENCY` 用 `asyncio.Semaphore` + `to_thread` 实现；模块加载时生成一次固定假哈希。短信或第三方注册的用户 `password_hash` 为空，`hasPassword` 由此得出。

### 7.4 角色与权限

- 角色、平台权限码与授予关系见约定 §5.5，写在 `security/rbac.py` 的常量中；签发方与验证方加载同一份平台常量；`listRoles`、`listPermissions` 从签发方本服务的常量生成
- 本服务追加私有权限码 `note:read`、`note:write`、`file:write`，授予 `user`，只在本服务生效；业务模块用 `rbac.register(permission, roles=[...])` 声明，`admin` 自动拥有
- `endpoint(permissions=[...])` 全部满足、`any_permissions=[...]` 任一满足；权限由 access 的 `roles` claim 经常量映射得出，不查库
- 用户与角色的关系存 `user_roles`，每个用户都有一行 `user`；`setUserRoles` 吊销该用户全部会话（约定 §5.5）

### 7.5 签发方 / 验证方

| 角色 | 判定（约定 §9） | 加载 | 迁移分支 |
|---|---|---|---|
| 签发方 | 设置 `AUTH_SIGNING_KEY` | `identity/*`、JWKS、`security/`、`modules/*` | `platform` + `identity` + `modules` |
| 验证方 | 设置 `AUTH_JWKS_URL` | `security/`；按已配置的依赖挂载模块（见下表） | 配置数据库时 `platform` + `modules` |

两者同时设置或都不设置时启动失败。签发方必须配置 `DATABASE_URL` 与 `STORAGE_DRIVER`（约定 §11 要求签发服务提供 §11.5）。验证方按依赖挂载：

| 验证方已配置 | 挂载 |
|---|---|
| 仅 JWT 相关变量 | `security/`（鉴权依赖、RBAC）与本服务自有的无状态路由 |
| + `DATABASE_URL` | `audit`、`notes`、`user.deleted` 消费者（清理本地数据） |
| + `DATABASE_URL` 与 `STORAGE_DRIVER` | `files`（按约定 §11.5） |
| + `REDIS_URL` | 吊销检查、限流、缓存、队列跨进程生效 |

验证方的最小配置（monorepo 中的 AI / 数据处理服务，不挂载需要数据库的模块）：

```dotenv
APP_ENV=production
OTEL_SERVICE_NAME=ai
AUTH_ISSUER=https://identity.example.com
AUTH_AUDIENCE=example-api
AUTH_JWKS_URL=http://identity:3000/.well-known/jwks.json
REDIS_URL=redis://redis:6379/0
# 需要自有数据或文件上传时再加：
# DATABASE_URL=postgresql://ai:***@postgres:5432/app?sslmode=disable
# DATABASE_SCHEMA=ai
# STORAGE_DRIVER=s3
# EVENTS_TRANSPORT=redis_stream
# EVENTS_REDIS_STREAM=events:user   # 由 monorepo 统一分配
```

- 未配置 `DATABASE_URL` 时不建连接池、`app migrate` 直接退出、不启动事件消费者；未配置 `STORAGE_DRIVER` 时不挂载 `files`
- `/health/ready` 只检查已配置的依赖（数据库、Redis），不检查 JWKS（约定 §1）
- `app openapi export` 以签发方与全部模块挂载的配置生成文档
- Redis 键：`auth:`、`rl:` 开头的键按约定原格式使用（含私有限流规则），其余一律带 `{OTEL_SERVICE_NAME}:` 前缀（约定 §10），由 `redis.key(...)` 统一拼接；Taskiq stream 名同样带前缀；事件 stream 名例外，见下条
- 事件：`infrastructure/events/` 提供 `EventPublisher` / `EventConsumer` 协议，`EVENTS_TRANSPORT=none|log|redis_stream`（默认 `log`）；stream 名 `EVENTS_REDIS_STREAM` 由 monorepo 配置、不加服务前缀，消费者组名为 `OTEL_SERVICE_NAME`（约定 §10）；签发方提交删除后发布 `user.deleted`，验证方的消费者调用本服务登记的清理器

## 8. 数据模型

列名 snake_case，主键 UUIDv7，时间 `timestamptz`，枚举存 `text` + CHECK，约束名用 `MetaData(naming_convention=...)`。只有 `users` 软删除，唯一索引带 `WHERE deleted_at IS NULL`。引用用户的业务表只存 `sub`，不建到 `users` 的外键（约定 §10，且验证方没有 `users` 表）。验证方未配置数据库时不建任何表。

| 表 | 主要列 | 分支 |
|---|---|---|
| `files` | owner_id（`sub`）、purpose、storage_key、name、content_type、size、created_at | platform |
| `audit_logs` | actor_id（`sub`）、action、resource_type、resource_id、request_id、ip、user_agent、changes（jsonb，已脱敏）、created_at；created_at 索引 | platform |
| `users` | email（可空，唯一）、email_verified_at（→ `emailVerified`）、phone（可空，E.164，唯一）、password_hash（可空，→ `hasPassword`）、display_name、avatar_file_id（→ `files`，`ON DELETE SET NULL`，输出为 `avatarUrl`）、status（`active` / `disabled`）、last_login_at、created_at、updated_at、deleted_at | identity |
| `user_roles` | user_id、role（`text`，取值由代码常量校验）；复合主键 | identity |
| `user_identities` | user_id、provider、openid（支付宝为 user_id）、union_id（可空）、created_at；（provider, openid）唯一；微信两个 provider 的 union_id 部分唯一索引 | identity |
| `sessions` | user_id、user_agent、ip（`inet`）、auth_time、created_at、last_active_at、expires_at（登录时确定）、revoked_at | identity |
| `refresh_tokens` | session_id、token_hash（唯一）、created_at、rotated_at、grace_ciphertext（可空） | identity |
| `verification_tokens` | user_id、purpose（`verify_email` / `reset_password`）、token_hash、password_fingerprint、expires_at；（user_id, purpose）唯一 | identity |
| `account_deletions` | user_id（主键）、requested_at、completed_at、attempts、last_error | identity |
| `notes` | owner_id（`sub`）、title、content、status（`draft` / `published`）、created_at、updated_at | modules |

迁移：

- 三个 Alembic 分支：`platform` 为基础；`identity`、`modules` 声明 `depends_on` platform（`users.avatar_file_id` 引用同 schema 的 `files`）
- `alembic upgrade` 只接受一个目标：签发方 `app migrate` 执行 `upgrade heads`，配置了数据库的验证方执行 `upgrade modules@head`（`depends_on` 带上 platform）；`app migrate` 先 `CREATE SCHEMA IF NOT EXISTS`，`version_table_schema` 为 `DATABASE_SCHEMA`
- ORM 表用 `info={"branch": ...}` 标注；`env.py` 的 `include_object` 按当前角色过滤，验证方的 `alembic check` 不比较 identity 表；新迁移用 `alembic revision --autogenerate --head <branch>@head`
- 初始迁移在 B3–B7 期间按需重新生成，B8 定稿；定稿后已合并的迁移不得修改
- CI 对两种角色各跑一遍 upgrade → check → downgrade base → upgrade（B7 起）
- `alembic.ini` 设置 `version_locations` 指向三个分支目录与 `recursive_version_locations = true`

## 9. 基础设施

| 能力 | 实现 |
|---|---|
| 日志 | structlog 处理器：`event`→`msg`、`warning`→`warn`，补约定 §8 的公共字段；访问日志用路由模板作 `route`；脱敏处理器丢弃令牌、密码、验证码字段，邮箱与手机号按约定掩码；开发 `LOG_FORMAT=text` 彩色单行 |
| 指标 | `telemetry/` 在导入任何 instrumentation 前 `setdefault` `OTEL_SEMCONV_STABILITY_OPT_IN=http`（Dockerfile 同样设置），使 FastAPI instrumentation 产出 `http.server.request.duration`，经 Prometheus exporter 输出为约定 §8 的指标名与标签；自定义 `queue.job.duration`、`ratelimit.fallback` |
| 指标出口 | 用标准变量 `OTEL_METRICS_EXPORTER`（默认 `prometheus`）。`prometheus`：API 在 `/metrics` 输出 `PrometheusMetricReader` 的注册表（`METRICS_TOKEN`），要求 `WEB_CONCURRENCY=1`，横向扩容靠副本；worker / scheduler 按 `OTEL_EXPORTER_PROMETHEUS_HOST` / `OTEL_EXPORTER_PROMETHEUS_PORT`（默认 `0.0.0.0:9464`）起独立端口。`otlp`：推送到 Collector，API 可多 worker，不挂载 `/metrics`（约定 §1 允许）。`OTEL_SDK_DISABLED=true` 时关闭链路与指标，同样不挂载 `/metrics` |
| 链路 / Sentry | 见约定 §8；Sentry 只由 `SENTRY_DSN` 控制，不受 `OTEL_SDK_DISABLED` 影响 |
| 健康检查 | `/health/live` 恒 200；`/health/ready` 只检查已配置的数据库与 Redis，失败 503 + `Retry-After` |
| 限流 | Redis Lua GCRA，键、值、`now`、主体、响应头、出错策略按约定 §7；客户端 IP 由 `common/client.py` 按 `TRUSTED_PROXIES`（层数或 CIDR 列表）解析；共享规则（约定 §11.6）在 `throttler/rules.py` 写成常量；私有规则名以 `{OTEL_SERVICE_NAME}-` 开头、键仍为 `rl:{规则名}:{主体}`，默认规则 `{OTEL_SERVICE_NAME}-default`，配额由 `RATE_LIMIT_DEFAULT` 配置；`endpoint(rate_limit=[...])` 引用规则，依赖计算结果存入请求状态，响应中间件写 `X-RateLimit-*`（取剩余最少的一条），429 只写 `Retry-After` |
| 缓存 / 锁 | `infrastructure/redis/`：`SET NX PX` + Lua 释放；`infrastructure/cache/`；键带服务前缀；未配置 Redis 时进程内实现 |
| 队列 | Taskiq + `RedisStreamBroker`，stream `{service}:tasks`；重试中间件指数退避；未配置 Redis 时 `InMemoryBroker`（仅开发 / 测试） |
| 定时任务 | Taskiq scheduler 独立进程，锁 `{service}:lock:cron:{name}`：清理过期会话及其 refresh 记录与过期一次性令牌、账号注销补偿 |
| 邮件 | aiosmtplib + Jinja2，模板 `verify-email`、`reset-password` 中英双语，链接基于 `APP_FRONTEND_URL`；提交后入队发送；未配置 `MAIL_HOST` 时只记日志 |
| 文件 | 接口与用途规则见约定 §11.5；签发方必挂，验证方配置数据库与存储时挂载（§7.5）。`STORAGE_DRIVER=local|s3`；用途表 `avatar`（约定）与 `attachment`（本服务追加）；上传路由豁免 `BODY_LIMIT`，以所有用途的最大上限作为流式读取上限，解析出 `purpose` 后按该用途上限判定 `file.tooLarge`；类型按文件头魔数识别（只需覆盖白名单内类型，不引入依赖）。`File.url`：s3 为预签名 GET；local 为 `GET /files/{id}/content?expires=&signature=`（`getFileContent`，HMAC-SHA256，密钥 `STORAGE_URL_SECRET`，不包信封，`FileResponse` 输出，响应头按约定 §11.5） |
| 审计 | `endpoint(audit=Audit("user.update", resource="user"))`；请求成功后独立事务写 `audit_logs`，失败只记日志；`GET /audit-logs`（`listAuditLogs`，`audit:read`，游标分页） |
| 账号注销 | `deleteMe` / `deleteUser`：同一事务内做 `role.lastAdmin` 检查，软删用户并清空 email / phone / password_hash / avatar_file_id（约定 §11.3 立即释放），删除 `user_roles`、`user_identities`、`verification_tokens`，吊销全部会话（写约定 §5.1 的吊销键），写 `account_deletions`；提交后入队清理任务并发布 `user.deleted`。清理器用 `register_account_cleaner(order)` 登记（files、notes），按 `sub` 清理且幂等；签发方删除 `sessions` / `refresh_tokens`；失败重试 5 次后记 error 并上报 Sentry；定时补偿未完成记录；`app users cleanup <id>` 手动重跑。验证方配置数据库时消费 `user.deleted` 并执行同一组本地清理器 |
| 进程 | api（Granian）、worker、scheduler、migrate（一次性） |

## 10. 配置

- 公共变量（含 `DATABASE_URL`、`DATABASE_SCHEMA`、`BODY_LIMIT`、`AUTH_VERIFY_KEYS`、`APP_FRONTEND_URL`、`OTEL_SERVICE_NAME`）见约定 §9，名称与语义不改；`DATABASE_URL` 的 libpq URI 在 `config/database.py` 转为 `postgresql+asyncpg://`，`sslmode` 转为 asyncpg 的 `ssl` 参数；缺少 `OTEL_SERVICE_NAME` 时启动失败
- 本服务特有变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_POOL_SIZE` / `DATABASE_POOL_TIMEOUT` | `10` / `10s` | 每进程连接池 |
| `WEB_CONCURRENCY` | `1` | Granian worker 数；`OTEL_METRICS_EXPORTER=prometheus` 时只能为 1 |
| `PASSWORD_HASH_CONCURRENCY` | `2` | 约定 §5.4 |
| `SMS_PROVIDER` / `SMS_ALIYUN_*` | `log` | |
| `WECHAT_MINIAPP_APP_ID` / `_SECRET`、`WECHAT_APP_APP_ID` / `_SECRET`、`ALIPAY_APP_ID` / `ALIPAY_PRIVATE_KEY` / `ALIPAY_PUBLIC_KEY` | 空 | 配齐即启用对应 provider |
| `STORAGE_DRIVER` / `STORAGE_LOCAL_DIR` / `STORAGE_URL_TTL` / `STORAGE_URL_SECRET` / `S3_*` | 空 / `./var/files` / `10m` / 空 | `local` / `s3`；签发方必填，验证方为空时不挂载 `files`；`local` 在生产必须设置 `STORAGE_URL_SECRET` |
| `MAIL_HOST` / `MAIL_PORT` / `MAIL_USER` / `MAIL_PASSWORD` / `MAIL_FROM` / `MAIL_TLS` | 空 / 587 | |
| `RATE_LIMIT_DEFAULT` | `10/s,burst=20` | 私有默认规则；共享规则不可配置 |
| `EVENTS_TRANSPORT` / `EVENTS_REDIS_STREAM` | `log` / 空 | 约定 §10；`redis_stream` 时 stream 名必填，由 monorepo 分配 |
| `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` | 空 | 生产 seed 必填，不设固定默认密码 |

- Settings 按分组拆分，敏感值 `SecretStr`，启动时一次性校验并列出全部错误；`APP_ENV=production` 时 `REDIS_URL`、`CORS_ORIGINS`、`METRICS_TOKEN`（`OTEL_METRICS_EXPORTER=prometheus` 时）必填，签发方另需 `APP_FRONTEND_URL`，`DOCS_ENABLED` 默认 false
- `.env.example` 按「公共 / 签发方 / 可选 provider / 基础设施」分段

## 11. 工程配置

| 项 | 做法 |
|---|---|
| 项目 | src 布局、`uv_build`、PEP 735 `dev` 依赖组 |
| Ruff | 保留 v1 规则集与 `FAST` 规则；`per-file-ignores` 与 `runtime-evaluated-base-classes` 去掉 `common/params.py`、`app.contract.schemas` |
| 类型 | mypy strict + pydantic 插件（门禁），ty 试运行 |
| pytest | 保留 v1 选项；markers：`unit`、`integration`、`conventions`、`api`、`e2e`、`committed` |
| 覆盖率 | branch，`fail_under = 85` |
| 钩子（prek） | pre-commit：ruff、typos、`uv lock --check`、gitleaks、yaml / toml；commit-msg：`scripts/check_commit_msg.py`；pre-push：`make check` |
| Makefile | `install`、`dev`、`serve`、`fmt`、`lint`、`typecheck`、`test-unit`、`check`、`test`、`test-conventions`、`test-api`、`test-e2e`、`openapi`（导出）、`migrate`、`seed`、`generate-resource`、`keys`、`infra`、`docker-build`、`up` / `down`、`security`、`workflow-lint`、`bench`、`ci`、`clean`（各目标的引入批次见 §14.2） |
| Docker | 保留 v1 多阶段镜像；`HEALTHCHECK` 指向 `/health/live`；`ENV OTEL_SEMCONV_STABILITY_OPT_IN=http`；上传目录 `/app/var/files` |
| 依赖更新 | Renovate 保留 |

## 12. 测试策略

| 层（marker） | 内容 | 依赖 |
|---|---|---|
| unit | 错误登记表与约定 §2.4 逐条一致、Pydantic 错误映射（每个 rule）、分页与游标、GCRA 计算、JWT 签发 / 校验、JWKS 缓存与刷新节流、宽限期加解密、Email / Phone / 时间 / UUID 类型、配置校验、生成器渲染 | 无 |
| integration | 每个接口的正常与错误路径；refresh 轮换、宽限期、重用吊销、并发刷新（`committed`）；`role.lastAdmin` 并发；限流（共享与私有规则、Redis 出错策略）；幂等键；队列与定时任务；邮件（Mailpit）；文件（local + MinIO，魔数识别、按用途上限）；账号注销链路与 `user.deleted`；`DATABASE_SCHEMA` 隔离；验证方三种挂载组合（无数据库、有数据库、有数据库与存储） | testcontainers（PG 18、Redis 8、MinIO、Mailpit） |
| conventions | `tests/conventions/`：逐条断言约定 §1–§8 与 §11 的可观察行为；平台部分用测试专用应用挂载探针路由，不依赖业务接口 | testcontainers |
| OpenAPI（unit） | `app openapi export --check` 与入库文件一致；operationId 规则；约定 §11 的 operationId 与字段全部存在；错误响应与 `bearerAuth` 声明 | 无 |
| api | schemathesis 按 `openapi/openapi.json` 对运行中的服务发请求，检查状态码与响应 schema | testcontainers |
| e2e | compose 起完整栈（api、worker、scheduler、migrate），走注册 → 验证 → 登录 → 刷新 → notes CRUD → 上传 → 注销 | Docker Compose |

## 13. CI / CD

| workflow | job / 内容 |
|---|---|
| `check.yml` | lint（ruff、typos、hadolint）、workflow-lint（actionlint、zizmor）、type-check、test（unit + integration + conventions，覆盖率）、migrations（B7 起覆盖签发方与验证方两种角色）、openapi（导出比对；PR 上用 oasdiff 对 base 分支报告破坏性变更，写入 job summary，不阻塞）、api（schemathesis）、security（pip-audit、gitleaks）、build、image-scan（Trivy）、e2e |
| `deploy.yml` | 保留；就绪轮询为 `/health/ready`（B6） |
| `cleanup-registry.yml` | 保留 |
| `nestjs-drift.yml` | 删除 |

本仓库不生成跨仓库共享产物；三个仓库只共享 `docs/platform-conventions.md`。

## 14. 从 v1 迁移的重构清单

「批次」对应 §15。

### 14.1 删除

| 路径 | 批次 | 说明 |
|---|---|---|
| `src/app/modules/{addresses,app_profile,config,consents,favorites,feedbacks,notifications,regions,home}/` | B1 | 9 个模块 |
| `src/app/infrastructure/alerting/`、`infrastructure/queue/notification.py`、`integrations/wechat/subscribe_value.py` | B1 | 告警与通知 |
| `src/app/infrastructure/account_cleanup/router.py` | B1 | 改为 CLI |
| `src/app/cli/seed_data/region-data.json` | B1 | 行政区划 |
| `scripts/{openapi-sync.sh,gen_validation_cases.py,schema_snapshot.py}`、`scripts/nestjs/`、`db/nestjs-schema.sql` | B1 | 同步工具 |
| `scripts/bench/` | B1 | 依赖 nestjs 对照环境；B8 以 `make bench` 重写 |
| `tests/parity/`、`tests/contract/`、`tests/fixtures/nestjs_mail/` | B1 | 对照测试与邮件快照 |
| `tests/unit/test_wechat_subscribe.py`、`tests/integration/test_{addresses,app_config,app_profile,consents,favorites,feedbacks,notifications,regions,account_cleanup_admin,schema}.py` | B1 | 对应功能删除 |
| `.github/workflows/nestjs-drift.yml`、`docs/contract.md` | B1 | |
| `src/app/common/validation/`、`common/{params,model_params,responses,exceptions}.py`、`src/app/contract/`、`scripts/gen_schemas.py` | B2 | 由 §6 的实现取代，B2 结束时删除 |
| `openapi/{nestjs-baseline.json,validation.json,SOURCE}` | B2 | 由 `openapi/openapi.json` 取代 |
| `src/app/core/users/serializers.py` | B2 | 由 Pydantic 响应模型取代 |
| `tests/unit/{test_validator_js,test_http_contract}.py` | B2 | 由 `tests/conventions/` 取代 |
| `src/app/infrastructure/database/schema_check.py`、`migrations/versions/*`（v1 迁移与 B1 的删表迁移） | B3 | 按 §8 分支重新生成 |
| `openapi/nest_handlers.json`、`scripts/gen_nest_handlers.py`、`tests/unit/test_gen_nest_handlers.py` | B6 | 限流键改为约定 §7 |
| `src/app/modules/files/send.py` | B6 | 改用 `FileResponse` |
| `files/`（本地上传目录，已 gitignore） | B6 | 改为 `var/files/` |

### 14.2 改写

**B1 删除业务与对照工具**

| 路径 | 改写要点 |
|---|---|
| `CLAUDE.md` / `AGENTS.md` | 删除「契约规则」一节与全部 nestjs 约定（基线、DTO 同名、HTTP 200、camelCase 列、Redis 键与 nestjs 一致、AppModule 顺序）；新增「约定优先：对外行为以 `docs/platform-conventions.md` 为准，新增 errorKey、共享规则或共享键须三仓库同步登记」；目录速查、测试分层、文档索引按本方案；后续批次随结构变化增量更新 |
| `src/app/factory.py`、`infrastructure/database/models.py`、`cli/seed.py`、`core/auth/service.py`、`infrastructure/account_cleanup/__init__.py`、`infrastructure/throttler/storage.py` | 去掉对被删模块、告警、通知的引用（告警改为 error 日志） |
| `src/app/infrastructure/queue/{__init__,worker}.py`、`infrastructure/scheduler/__init__.py` | 去掉 `NOTIFICATION_QUEUE`、`queue.notification` 导入与 `cleanup-read-notifications` 定时任务 |
| `migrations/versions/0002_drop_business_tables.py`（新增） | 删除被删模块的表，使 CI migrations job 的 `alembic check` 继续通过；B3 随全部迁移一起重新生成 |
| `tests/integration/{test_background_jobs,test_data_layer}.py`、`tests/unit/{test_queue_infra,test_http_contract}.py` | 去掉被删模块、告警、通知、nestjs 邮件快照相关用例 |
| `src/app/config/base.py`、`config/wechat.py`、`scripts/check_commit_msg.py`、`.env.example` | 去掉 nestjs 字样的 docstring；删除 `WECHAT_SUBSCRIBE_*` |
| `src/app/integrations/wechat/__init__.py`、`infrastructure/metrics/__init__.py`、`cli/seed_data/seed.json` | 删除订阅消息发送（`send_subscribe_message`）、`admin_alerts_*` 指标、`config` 种子数据 |
| `tests/integration/{test_throttler,test_metrics,test_health}.py`、`tests/unit/{test_observability,test_http_contract}.py`、`tests/conftest.py` | 依赖 `home` 路由（`GET /`、`GET /api/health`）的用例改用 `GET /api/v1/health/live`；目录 marker 删 `contract`、`parity` |
| `Makefile`（删 `test-contract`、`test-parity`、`openapi-sync`）、`pyproject.toml`（markers 删 `contract`、`parity`；typos 排除删 `db/*.sql`；dev 依赖删 `uvicorn`）、`.github/workflows/check.yml`（删 contract、parity job）、`.github/renovate.json`（删 `ignorePaths`）、`.pre-commit-config.yaml`（`exclude` 删 `tests/contract/fixtures/`） | 与删除同步 |
| `README.md`、`docs/{architecture,cli,database,deploy,testing}.md` | 删除对已删模块、脚本、Make 目标、job 与 `docs/contract.md` 的引用；整体改写仍在 B8 |

**B2 平台约定**

| 路径 | 改写要点 |
|---|---|
| `src/app/common/{errors,handlers,routing,middleware,pagination,openapi,docs,context}.py`、`common/i18n/` | 按 §6；新增 `envelope.py`、`validation.py`、`types.py`、`idempotency.py`；i18n 改为 errorKey 与 rule 键；旧包 `common/validation/` 与新模块 `common/validation.py` 同名，在迁移开始时先删除 |
| `src/app/core/users/router.py` | `getUsers` 改名 `listUsers`，查询参数改为 `page`、`pageSize`、`sort`、`roleId`，响应为 `Page`；查不到用户时 404 `user.notFound` |
| `src/app/infrastructure/database/errors.py`、`core/auth/service.py` | 唯一约束冲突统一为 409 `user.emailTaken` / `user.phoneTaken` 留到 B4：B2 中服务层预检仍为 422 `emailAlreadyExists` / `phoneAlreadyExists`，并发写入命中约束时为 409 |
| `src/app/infrastructure/health/router.py` | 不包信封、不进入 OpenAPI；失败 503 只返回 `{"status":"error"}`，明细写日志；路径调整在 B6 |
| `src/app/cli/openapi.py` | `app openapi export [--check]`，导出时固定标题与文件驱动，结果不随本地环境变化 |
| `src/app/core/users/{repository,schemas,service,router}.py`、`core/auth/{router,service}.py`、`modules/files/{router,schemas,service,uploads,purposes,send}.py`、`infrastructure/{throttler/guard,metrics/guard,audit/__init__,queue/mail,health/router}.py`、`infrastructure/logging/`、`integrations/*`、`cli/templates/resource/*`、`tests/integration/test_background_jobs.py` | 全部改用 `AppError`、新 routing、`ApiResponse` 与 Pydantic 模型（路径与业务逻辑暂不变），之后才删除旧的 common 模块与 `contract/` |
| `src/app/config/app.py` | `SWAGGER_ENABLED` → `DOCS_ENABLED`；`APP_FALLBACK_LANGUAGE`（默认 en）→ `APP_DEFAULT_LOCALE`（默认 zh）；`FRONTEND_DOMAIN` → `CORS_ORIGINS`；删除 `API_PREFIX`、`OPENAPI_BASELINE_PATH`；新增 `BODY_LIMIT`；`FRONTEND_DOMAIN` 的邮件链接用途改由 `APP_FRONTEND_URL` 承担（自 B4 提前） |
| `pyproject.toml` | ruff `per-file-ignores` 与 `runtime-evaluated-base-classes` 去掉 `common/params.py`、`app.contract.schemas`；markers 增加 `conventions`；dev 依赖增加 `polyfactory` |
| `Makefile`、`.pre-commit-config.yaml` | `lint` 去掉 `gen_schemas.py --check`，新增 `openapi`、`test-conventions`；pre-commit `exclude` 去掉 `openapi/` |

**B3 数据层**

| 路径 | 改写要点 |
|---|---|
| `src/app/infrastructure/database/{base,session,models}.py` | UUIDv7、`timestamptz`、命名约定、schema、`info.branch` |
| `src/app/core/users/models.py`、`core/session/models.py`、`core/permissions/models.py`、`modules/files/models.py`、`infrastructure/audit/models.py`、`infrastructure/account_cleanup/models.py` 及其 repository / service / schemas | 列名 snake_case、主键 UUIDv7、时间 `timestamptz`；id 类型由 `int` 改为 `UUID` |
| `src/app/config/database.py` | `DATABASE_HOST` / `PORT` / `USERNAME` / `PASSWORD` / `NAME`、`DATABASE_SSL_*`、`DATABASE_CA` / `KEY` / `CERT` / `REJECT_UNAUTHORIZED`、`DB_SCHEMA_OWNER`、`DATABASE_MAX_CONNECTIONS`、`DATABASE_CONNECTION_TIMEOUT_MS`、`WORKER_DATABASE_MAX_CONNECTIONS` → `DATABASE_URL`（libpq）、`DATABASE_SCHEMA`、`DATABASE_POOL_SIZE` / `DATABASE_POOL_TIMEOUT` |
| `migrations/env.py`、`alembic.ini` | 多分支、`include_object`、`version_table_schema`、`CREATE SCHEMA`；`version_locations` 与 `recursive_version_locations` |
| `src/app/factory.py`、`tests/integration/test_data_layer.py` | 去掉 `schema_check` |

**B4 身份核心**

| 路径 | 改写要点 |
|---|---|
| `src/app/core/{auth,users,session,permissions}/`、`common/security.py` | 迁到 `identity/` 与 `security/`，接口按约定 §11，令牌与表按 §7、§8 |
| `src/app/integrations/mail/` | 迁到 `infrastructure/mail/`，模板 `verify-email`、`reset-password`；单元测试断言两个模板中英文的主题、正文与链接 |
| `src/app/infrastructure/throttler/guard.py`、`infrastructure/audit/__init__.py`、`infrastructure/logging/middleware.py`、`modules/files/{router,purposes}.py`、`infrastructure/queue/{mail,worker}.py`、`common/context.py`、`factory.py` | 导入改为 `identity/`、`security/`、`infrastructure/mail/` |
| `src/app/config/auth.py` | `AUTH_JWT_SECRET`、`AUTH_REFRESH_SECRET`、`AUTH_FORGOT_SECRET`、`AUTH_CONFIRM_EMAIL_SECRET`、`AUTH_*_EXPIRES_IN`、`PASSWORD_HASHER` → 约定 §9 的 `AUTH_*` 与 `PASSWORD_HASH_CONCURRENCY` |
| `src/app/config/mail.py`、`config/app.py` | `MAIL_DEFAULT_EMAIL` / `MAIL_DEFAULT_NAME` → `MAIL_FROM`；`MAIL_SECURE` / `MAIL_IGNORE_TLS` / `MAIL_REQUIRE_TLS` → `MAIL_TLS`（`APP_FRONTEND_URL` 已在 B2 引入） |
| `src/app/cli/{__init__,seed}.py`、`seed_data/seed.json` | `keys generate`、`users cleanup`；seed 只建管理员账号（角色为代码常量） |
| `pyproject.toml` | `pwdlib[argon2,bcrypt]` → `pwdlib[argon2]`；dev 依赖删 `aiosmtpd`（集成测试改用 Mailpit 容器） |
| `.gitleaks.toml` | 去掉 MD5 固定值白名单，改为 Ed25519 测试密钥白名单 |

**B5 短信与可选 provider**

| 路径 | 改写要点 |
|---|---|
| `src/app/integrations/{sms,wechat,alipay}/` | 迁到 `identity/providers/`，实现 `ProviderClient` 与错误映射 |
| `src/app/config/sms.py` | `SMS_MOCK_MODE`、`SMS_ACCESS_KEY_*`、`SMS_SIGN_NAME`、`SMS_TEMPLATE_CODE`、`SMS_ENDPOINT` → `SMS_PROVIDER`、`SMS_ALIYUN_*`；删除 `SMS_CODE_LENGTH`、`SMS_CODE_EXPIRES`、`SMS_MAX_ATTEMPTS`、`SMS_RESEND_INTERVAL`（规则由约定 §11.1 固定） |
| `src/app/config/{wechat,alipay}.py` | `WECHAT_MINI_APP_ID` / `_SECRET` → `WECHAT_MINIAPP_APP_ID` / `_SECRET`；`WECHAT_APP_ID` / `_SECRET` → `WECHAT_APP_APP_ID` / `_SECRET`；删除 `WECHAT_MINI_PROGRAM_STATE`；支付宝增加 `ALIPAY_PUBLIC_KEY` |

**B6 基础设施**

| 路径 | 改写要点 |
|---|---|
| `src/app/infrastructure/{logging,metrics,observability}/`、`src/app/__main__.py`、`infrastructure/queue/worker.py` | 合并为 `logging/` + `telemetry/`；`__main__` 去掉 `prepare_multiprocess`，校验 `WEB_CONCURRENCY` 与 `OTEL_METRICS_EXPORTER`；worker 改用 `telemetry`、`identity/account_deletion` |
| `src/app/infrastructure/{throttler,health,queue,scheduler,audit,cache,redis}/` | 按 §9；`redis/` 增加 `key()` 前缀函数 |
| `src/app/common/client.py` | `TRUSTED_PROXIES` 支持层数与 CIDR 列表 |
| `src/app/infrastructure/account_cleanup/` | 迁到 `identity/account_deletion/`，增加 `user.deleted` 发布；新增 `infrastructure/events/` |
| `src/app/modules/files/`（router、service、storage、urls、uploads、purposes、models） | 按约定 §11.5 与 §9「文件」 |
| `src/app/config/{redis,file,log,metrics,observability,throttler,app}.py` | `REDIS_HOST` / `PORT` / `DB` / `PASSWORD` / `ENABLED` → `REDIS_URL`；`FILE_DRIVER`、`FILE_*_MAX_SIZE`、`ACCESS_KEY_ID`、`SECRET_ACCESS_KEY`、`AWS_*` → `STORAGE_*`、`S3_*`；`LOG_PRETTY_PRINT` → `LOG_FORMAT`；删除 `METRICS_ENABLED`、`METRICS_PATH`、`APP_NAME`（指标标签）；`OTEL_ENABLED` → 标准 `OTEL_*`，新增必填 `OTEL_SERVICE_NAME`；`THROTTLE_*` → `RATE_LIMIT_DEFAULT`；`TRUST_PROXY` → `TRUSTED_PROXIES`；删除 `BACKEND_DOMAIN` |
| `pyproject.toml` | 新增 `opentelemetry-exporter-prometheus`、`cloudevents`；删除直接依赖 `prometheus-client`、`psutil` |
| `Dockerfile`、`docker/docker-compose.{base,redis,ecs}.yaml`、`.dockerignore`、`.gitignore` | Dockerfile 去掉 `/tmp/prometheus` 与 `COPY openapi`，`/app/files` 改 `/app/var/files`，加 `ENV OTEL_SEMCONV_STABILITY_OPT_IN=http`；compose 去掉 `PROMETHEUS_MULTIPROC_DIR`、共享 PID 命名空间、`prometheus-multiproc` 卷，`uploaded-files` 卷改挂 `/app/var/files`；`.dockerignore` 去掉 `!openapi/`、`!db/`；`.gitignore` 的 `/files/` 改 `/var/` |
| `Dockerfile`（HEALTHCHECK）、`docker/docker-compose.base.yaml`（healthcheck）、`deploy/manage.sh`、`.github/workflows/deploy.yml`、`tests/e2e/test_stack.py` | 健康检查路径改为 `/health/live`、`/health/ready` |

**B7 示例与验证方**

| 路径 | 改写要点 |
|---|---|
| `src/app/cli/{generate.py,templates/resource/*}` | 模板对齐 `notes` |
| `.github/workflows/check.yml` | migrations job 改为签发方与验证方两种角色 |

**B8 交付**

| 路径 | 改写要点 |
|---|---|
| `docker/*.yaml`（Mailpit、MinIO）、`.github/workflows/check.yml`（openapi、api job） | 按 §13 |
| `pyproject.toml` | dev 依赖新增 `schemathesis`，markers 增加 `api` |
| `README.md` | 定位为独立 Python 模板；功能、Make 目标、签发 / 验证方配置、文档索引 |
| `docs/architecture.md`、`auth.md`、`database.md`、`testing.md`、`cli.md`、`deploy.md`、`benchmark.md` | auth 引用约定 §5、§11 并写实现落点；database 删除「与 nestjs 共用数据库」，写 schema 与迁移分支；testing 删除契约与 parity 两节；cli 写新命令；deploy 写密钥生成与轮换、monorepo 部署；benchmark 重新压测 |

**各批通用**：`tests/conftest.py`、`tests/{unit,integration,e2e}/*` 随对应实现改写；`.env.example` 随变量变化更新。

### 14.3 保留

`src/app/main.py`、`scripts/audit_severity.py`、`tests/unit/test_commit_msg.py`、`.editorconfig`、`.vscode/`、`.python-version`、`.github/zizmor.yml`、`.github/workflows/cleanup-registry.yml`、`LICENSE`、`docs/platform-conventions.md`（不改）。

## 15. 实施阶段

每批结束停在工作区，`make check`、`make test` 与该批验收通过后由用户 review。

| 批 | 内容 | 验收 |
|---|---|---|
| B1 删除业务与对照工具 | §14 中 B1 的条目；保留 nestjs 基线与校验引擎，直到 B2 的替代实现就绪 | `make check`、`make test` 通过；CI 的 migrations job 通过、无 contract / parity job；CLAUDE.md 不含 nestjs 契约规则 |
| B2 平台约定 | 信封、错误码、校验转换、400 / 404 / 405 / 413 / 415、401 头、Request ID、i18n、CORS、分页、幂等键、类型序列化、code-first OpenAPI 与 `app openapi export`；全部调用方迁到新实现后删除旧模块 | `tests/conventions` 中信封、错误码、校验、405 / 413 / 415、请求头、分页、类型序列化部分（探针路由）通过；`openapi/openapi.json` 入库且 `--check` 通过 |
| B3 数据层 | §14 中 B3 的条目 | platform 分支 upgrade → check → downgrade → upgrade 通过；两个 schema 并存的集成测试通过；既有接口测试通过 |
| B4 身份核心 | EdDSA + JWKS、吊销键、会话与 refresh 表、约定 §11.1 邮箱部分、§11.2（除手机号绑定）、§11.3、角色常量、seed、邮件模板 | 约定 §5 与 §11 对应部分断言通过；`committed` 并发刷新、重用检测、`role.lastAdmin` 测试通过 |
| B5 短信与可选 provider | `sendSmsCode`、`loginWithSms`、`bindPhone`、`listAuthProviders`、`loginWithProvider`、`bindPhoneWithWechat` | 未配置时 `listAuthProviders` 为空、`loginWithProvider` 返回 `auth.providerDisabled`；mock 第三方的集成测试（含 `providerCodeInvalid`、`badGateway`、unionid 回填）通过；约定 §11.1–§11.3 全部断言通过 |
| B6 基础设施 | 限流、日志、OTel 指标、Sentry、健康检查、队列 / 定时、文件、审计、账号注销与 `user.deleted` | 约定 §6–§8、§11.5、§11.6 断言通过；`/metrics` 含 `http_server_request_duration_seconds` 及约定标签；Mailpit 收到中英邮件；MinIO 上传通过；e2e 用新健康检查路径通过 |
| B7 示例与验证方 | `notes`、生成器、验证方模式、事件消费 | 生成器产出模块通过 lint 与测试；两种角色的 migrations 四步通过；无数据库的验证方只挂载 `security/` 且 ready 通过；有数据库的验证方用签发方 JWKS 访问 `notes`、收到 `user.deleted` 后清理数据 |
| B8 交付 | compose、workflow、schemathesis、oasdiff、README / docs、压测、迁移定稿 | `make ci` 本地通过；e2e 通过；`rg -i 'nestjs|baseline|class-validator' --glob '!TEMPLATE_PLAN.md'` 无结果 |

## 16. 待决策

| # | 问题 | 建议 |
|---|---|---|
| 1 | 业务域错误码 | 已登记于约定 §2.4；新增条目先在约定中登记再实现 |
| 2 | `/metrics` 与多 worker | 默认单 Granian worker、靠副本扩容；需要单容器多 worker 时 `OTEL_METRICS_EXPORTER=otlp` |
| 3 | 是否重写初始迁移 | 重写；模板无生产数据。已基于 v1 部署过的实例需重建库 |
| 4 | 更换邮箱 | 约定 §11 未提供，本期不做；手机号经 `bindPhone` 绑定或更换 |
| 5 | 对象存储直传 | 本期不做（约定 §11.5 允许扩展）；需要时新增私有接口并在 `files` 表加 status |
| 6 | ty 替代 mypy | ty 1.0 发布后评估 |

## 17. 风险

| 风险 | 应对 |
|---|---|
| go-template / springboot-template 从本仓库复制共享产物（`openapi/*`、`db/nestjs-schema.sql`、`validation-cases.json`、`openapi-sync.sh`），B1 删除后其同步流程会断 | 三个仓库按各自 v2 方案同样删除共享产物与比对流程；B1 前由协调窗口确认另两个仓库已去掉对本仓库产物的依赖 |
| B2 改动面大（common 层被几乎所有模块引用） | B2 内先实现新模块，再逐个迁移调用方，最后删除旧模块；每迁移一组跑一次 `make check` |
| 三个仓库对同一条约定理解不一 | `tests/conventions` 逐条断言；新增 errorKey、共享规则、共享键时先改约定再实现 |
| Pydantic 错误类型到 `rule` 的映射遗漏 | 未匹配类型落到 `format` 并记 warn 日志；单元测试覆盖每种约束 |
| OTel 默认产出旧指标名 | 启动时 `setdefault` `OTEL_SEMCONV_STABILITY_OPT_IN=http`，B6 验收断言指标名与标签 |
| OTel Prometheus exporter 为 beta（0.66b2） | 可切换为 OTLP 出口 |
| 宽限期密文存在数据库 | 密钥只由旧 refresh 明文派生，库内无法解密；会话过期清理时一并删除 |
| 删除范围大，遗漏引用 | 每批以 `make check`、`make test` 验收；B8 全文搜索 nestjs 相关关键字 |

## 18. 版本核对来源（2026-11-17）

- PyPI：fastapi 0.163.2、fastapi-cli 0.0.34、starlette 1.10.0、pydantic 2.14.3、pydantic-settings 2.16.3、sqlalchemy 2.1.9、asyncpg 0.32.0、alembic 1.20.1、redis 8.1.0、taskiq 0.13.0、taskiq-redis 1.2.4、pyjwt 2.15.1、cryptography 50.0.2、pwdlib 0.4.2、structlog 26.1.0、opentelemetry-sdk 1.45.2、opentelemetry-exporter-otlp-proto-http 1.45.2、opentelemetry-instrumentation-fastapi / sqlalchemy 0.66b2、opentelemetry-exporter-prometheus 0.66b2（依赖 prometheus-client）、sentry-sdk 2.75.0、granian 2.8.8、orjson 3.13.0、typer 0.29.1、httpx 0.28.1、aioboto3 15.5.0、aiosmtplib 5.1.3、jinja2 3.1.6、python-multipart 0.0.34、cloudevents 2.2.0、connectrpc 0.13.0（依赖 protobuf-py、pyqwest）、pytest 9.2.0、pytest-asyncio 1.5.0、pytest-xdist 3.8.0、coverage 7.17.3、testcontainers 4.15.0、time-machine 3.6.0、polyfactory 3.5.0、httpx2 2.16.2、schemathesis 4.33.4、ruff 0.17.5、mypy 2.4.1、ty 0.0.105、uv 0.13.9、prek 0.5.11、typos 1.52.2、zizmor 1.32.0、pip-audit 2.10.1
- endoflife.date：Python 3.15.2（2026-10-25）、PostgreSQL 18.7（2026-11-09）、Redis 8.10.3（2026-10-26）
