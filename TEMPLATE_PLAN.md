# fastapi-temp 改造方案

> 目标：把 fastapi-temp 从「官方 full-stack-fastapi-template 克隆」改造成 Python 后端模板：
>
> 1. 技术栈取当前最新稳定版；
> 2. 与 nestjs-template **API 契约一致**，vite-vue3-temp、uniapp-template 通过 Orval 生成的前端可无改动切换；在不破坏契约的前提下，采纳比 nestjs-template 更好的方案（§5）；
> 3. 工程配置采用业界当前最佳实践（§10）；
> 4. CI 结构参照 nestjs-template（§11）。
>
> 编写日期：2026-11-10。版本数据来自 PyPI / endoflife.date / GitHub API 当日查询结果。
>
> 契约基准：`xingyunqi-tech/nestjs-template`（公开仓库）`release/exam-prep` 分支 `02fdb4d`。下文引用的 nestjs 源码均指该提交；基线升级见 §4.3、§11.5。

---

## 0. 现状

| 项 | 状态 |
|---|---|
| 上游 | `fastapi/full-stack-fastapi-template`，46.2k★，最近提交 2026-11-03，最新 release 0.12.0（2026-08-12） |
| 本地 | 已快进到上游 `aabb901`（2026-11-03），无本地提交；`origin` 仍指向上游 |
| 上游后端 | 同步 SQLModel + psycopg、单文件 `models.py` / `crud.py`、仅邮箱登录、JWT 单令牌（8 天）、无统一响应、无 i18n / 限流 / 指标 / 缓存 / 队列 |
| 上游其余部分 | React 前端（`frontend/`）、react-email（`packages/`）、bun、Traefik、FastAPI Cloud 部署、15 个 GitHub workflow |

上游可复用：uv 用法、Alembic 配置、Dockerfile 的 uv 分层写法、pydantic-settings 模式、prek / zizmor / typos 配置。其余按本方案重建。

---

## 1. 范围

### 1.1 本期做（对齐 nestjs-template `src/core` + `src/infrastructure` + 通用模块）

- 统一响应 / 错误 / 校验错误格式、i18n、Request ID、CORS、安全头、限流
- 认证：邮箱、手机号密码、短信验证码、微信（小程序 / App / 手机号）、支付宝；JWT 双令牌 + 刷新轮换 + 会话
- 用户管理、RBAC 权限、角色 / 状态
- 文件上传（local / s3 / s3-presigned）、私有文件访问
- 健康检查、Prometheus 指标、结构化日志、Sentry、OpenTelemetry
- Redis 缓存 + 分布式锁、任务队列、定时任务、审计日志、告警、账号清理
- 邮件（注册确认、找回密码；nestjs 的 Handlebars 模板改写为 Jinja2，中英双语）
- Seed 数据：角色、状态、权限、管理员与示例用户，账号与 nestjs seed 一致，前端模板开箱可登录
- 通用业务模块：config、regions、notifications、addresses、favorites、feedbacks、consents、app-profile
- 模块代码生成器（对应 nestjs 的 `generate:resource`）
- Docker / Compose 分环境、deploy/manage.sh、GitHub Actions

### 1.2 本期不做

nestjs-template `src/modules` 里的考试刷题业务：exams、questions、practice、papers、imports、errata、wrong-book、study-stats、question-notes、ai、game-auth、monitor。orders、payments、shares 见 §13 待决策。

不做的接口仍完整出现在 `/docs-json` 中（前端生成代码依赖，见 §4.3），请求时返回 404 信封，清单记录在 `docs/contract.md`。

---

## 2. 技术栈（当前最新稳定版）

| 类别 | 选型 | 版本（2026-11-10） | 说明 |
|---|---|---|---|
| 语言 | Python | **3.15**（3.15.2） | M0 已验证 asyncpg / pydantic-core / granian / argon2-cffi / orjson 在 macOS 与 manylinux_2_40 均有 cp315 wheel（orjson 仅发 manylinux_2_39，`python:3.15-slim` 满足）；`requires-python = ">=3.15"` |
| 包管理 | uv | 0.13.7 | `.python-version` 锁解释器，`uv.lock` 锁依赖 |
| Web 框架 | FastAPI | 0.162.4 | 上游锁在 0.141.1 |
| ASGI 底层 | Starlette | 1.10.0 | |
| 校验 | Pydantic | 2.14.3 | |
| 配置 | pydantic-settings | 2.16.2 | |
| ORM | **SQLAlchemy 2.1（async）** | 2.1.8 | 弃用 SQLModel：仍是 0.0.x，上游因此锁在 SQLAlchemy 2.0.51，且只用同步会话 |
| 驱动 | asyncpg | 0.32.0 | |
| 迁移 | Alembic | 1.20.x | async env.py |
| 数据库 | PostgreSQL | 18（18.6） | 独立部署用 18；与 nestjs 共库、parity 对照时用 17.11（nestjs compose 与 CI 均为 `postgres:17.11-alpine`） |
| 缓存 / 锁 | Redis + redis-py | 8.10 / 8.1.0 | `redis.asyncio`；nestjs 现为 Redis 7.4，与其共用实例时按 7.x 兼容使用 |
| 队列 + 定时 | Taskiq + taskiq-redis | 0.13.0 / 1.2.4 | 原生 async，自带 scheduler；arq 2026-04 后无发布，Celery 非原生 async |
| JWT | PyJWT | 2.15.1 | |
| 密码哈希 | pwdlib[argon2,bcrypt] | 0.3.1 | 见 §5 第 1 项 |
| HTTP 客户端 | httpx | 0.28.1 | 1.0 仍是 dev 版 |
| 邮件 | aiosmtplib + Jinja2 | 5.1.3 | |
| 对象存储 | aioboto3 | 15.5.0 | S3 兼容，阿里云 OSS 走 endpoint |
| 日志 | structlog | 26.1.0 | |
| 指标 | prometheus-client | 0.26.0 | 指标名与 nestjs 一致 |
| 链路 | OpenTelemetry（instrumentation-fastapi / sqlalchemy / redis / httpx） | 0.66b2 | `OTEL_ENABLED` 开关 |
| 错误上报 | sentry-sdk[fastapi] | 2.74.0 | |
| 服务器 | Granian | 2.8.7 | 生产多 worker；本地 `fastapi dev` |
| JSON | orjson | 3.13.0 | |
| CLI | Typer | 0.29.0 | |
| 测试 | pytest / pytest-asyncio / pytest-xdist / coverage | 9.2.0 / 1.4.1 / 3.8.0 / 7.17.2 | |
| 测试容器 | testcontainers | 4.15.0 | |
| 测试辅助 | polyfactory、time-machine、syrupy | 最新 | 测试数据、冻结时间、快照 |
| Lint / 格式化 | Ruff | 0.17.4 | |
| 类型检查 | mypy strict（门禁）+ ty（并行试运行） | 2.3.x / 0.0.100 | ty 仍是 0.0.x 预览版 |
| Git 钩子 | prek | 0.5.x | |
| 依赖更新 | Renovate | — | 一份配置覆盖 uv、Docker 镜像、GitHub Actions |

### 2.1 不选的方案

| 方案 | 原因 |
|---|---|
| Litestar | 契约依赖的 OpenAPI 细节（operationId、tag）在 FastAPI 下更好控制，生态更大 |
| Django / django-ninja | 与 nestjs 分层差异大 |
| fastapi-users | 登录方式、令牌、会话都要按 nestjs 定制 |
| fastapi-pagination | 分页形状固定，自写即可 |
| slowapi | 0.1.x，维护慢；限流 key 规则需定制 |
| Dependabot | 不支持跨生态分组和自动合并策略，Renovate 更灵活 |

---

## 3. 目录结构

采用 **src 布局**（uv `--package` 默认、防止测试误导入未安装的源码）。

```
fastapi-temp/
├── src/app/
│   ├── main.py                    # create_app()：lifespan、中间件、异常处理、路由
│   ├── cli/                       # Typer：seed、permissions clear-cache、createsuperuser、generate module
│   ├── config/                    # Settings 按分组拆分
│   ├── common/
│   │   ├── responses.py           # 统一响应包装
│   │   ├── exceptions.py          # BusinessException、错误键表
│   │   ├── handlers.py            # 全局异常处理
│   │   ├── pagination.py
│   │   ├── routing.py             # ApiRoute：operationId、状态码（默认 200，upload 201）、鉴权声明、包装开关；404 替代 405
│   │   ├── security.py            # md5 校验、密码哈希、JWT
│   │   ├── deps.py                # CurrentUser、OptionalUser、require_permissions、DbSession
│   │   └── i18n/{en,zh}/common.json   # 复用 nestjs 翻译文件
│   ├── core/{auth,users,permissions,session}/
│   ├── infrastructure/{database,cache,redis,lock,queue,logger,metrics,health,tracing,audit,alerting,account_cleanup,throttler,files}/
│   ├── integrations/{mail,sms,wechat,alipay}/
│   └── modules/                   # config、regions、notifications、addresses、favorites、feedbacks、consents、app_profile
├── migrations/                    # Alembic
├── tests/{unit,integration,e2e,contract,parity}/
├── openapi/
│   ├── nestjs-baseline.json       # nestjs /docs-json 完整原文（FILE_DRIVER=local），本服务 /docs-json 逐字节输出它
│   ├── validation.json            # nestjs class-validator 元数据导出（DTO → 字段 → 有序约束 / 转换 / 消息），Python / Go / Java 共用
│   └── SOURCE                     # nestjs 仓库、分支、提交号（当前 02fdb4d）、导出环境（FILE_DRIVER=local）
├── db/nestjs-schema.sql           # nestjs 迁移跑完后的表结构导出，表结构比对基线
├── templates/module/              # 代码生成器模板
├── docker/  deploy/  scripts/
├── .github/{workflows,renovate.json,ISSUE_TEMPLATE}
├── Dockerfile  .dockerignore
├── pyproject.toml  uv.lock  .python-version  .env.example  .editorconfig
├── .vscode/{settings,extensions}.json
├── Makefile                       # 统一入口：make dev / check / test / ...
├── README.md  CLAUDE.md  AGENTS.md  LICENSE
└── docs/
```

### 3.1 模块内分层（对应 nestjs 六边形架构）

```
core/users/
├── domain.py          # 纯领域对象（dataclass）
├── models.py          # SQLAlchemy ORM
├── mappers.py         # ORM ↔ domain
├── repository.py      # Protocol 端口 + SQLAlchemy 实现
├── schemas.py         # 请求 / 响应模型（me / admin 两套输出）
├── service.py         # 只依赖 repository 端口
└── router.py          # 薄路由
```

规则同 nestjs CLAUDE.md：domain 不引用 ORM；路由不写业务；配置只从 Settings 读取。

---

## 4. API 契约（与 nestjs-template 对齐）

### 4.1 全局

| 项 | 约定 |
|---|---|
| 前缀 | `/api/v1/...`（`API_PREFIX`，默认 `api`）；`GET /`、`GET /api/health`、`GET /metrics`（`METRICS_PATH`）不带版本 |
| 端口 | 默认 `APP_PORT=3000`，与 nestjs 及两个前端的 Orval target / dev 环境（`localhost:3000`）一致 |
| 成功响应 | `{"code": 200, "message": "success", "data": <payload>}`；**成功请求一律 HTTP 200**（含 POST；OpenAPI 中标 `@ApiCreatedResponse` 的接口也返回 200），**唯一例外是 `POST files/upload`：HTTP 201、body `code` 仍为 200**（nestjs 三个上传 controller 均未加 `@HttpCode`，`test/platform/platform-foundation.e2e-spec.ts` 断言 201）；`void` 接口不输出 `data` 键 |
| 已是信封的返回 | 原样透传 |
| 跳过包装 | 路由级 `wrap=False`：`/metrics`、SSE、支付回调 |
| 错误响应 | `{"code": <HTTP 状态>, "message": <已翻译文本>, "data": <对象或 null>}`，无独立业务码；`message` / `data` 的翻译规则见下方「错误翻译规则」 |
| 默认消息 | 400/401/403/404/409/410/422/429/500 → i18n `common.<key>`（`badRequest`、`unauthorized`、`forbidden`、`notFound`、`conflict`、`gone`、`unprocessableEntity`、`tooManyRequests`、`internalServerError`）；表外状态码输出 Nest `HttpStatus` 枚举名原文、不翻译（如 413 → `PAYLOAD_TOO_LARGE`、503 → `SERVICE_UNAVAILABLE`） |
| 业务错误 | `BusinessException(error_key, **details)` → `message` 为 `common.<errorKey>` 译文，`data: {...details, "errorKey": key}` 不翻译，状态码查表（键表从 nestjs `business-error-keys.ts` 复制） |
| 校验错误 | 422，`message` 为 `common.unprocessableEntity` 译文（en `Validation failed` / zh `数据验证失败`）；`data: {字段: 消息}`，同字段所有失败约束都输出，按 class-validator 装饰器顺序（在 `@Transform` 之后执行）以 `", "` 拼接，约束与消息来源见 §4.3 的 `validation.json`；嵌套对象为嵌套 map；顶层字符串值同样按翻译规则尝试 `common.<value>`；未知字段剔除不报错 |
| 非法 `:id` | 400，`message` 为 `common.badRequest` 译文，`data: {"id": <common.invalidId 译文>}`（en `Invalid id` / zh `ID 格式不正确`），对应 nestjs `ParseIdPipe`（`^\d+$`、1 ≤ id ≤ 2147483647）；FastAPI 对 id 路径参数的校验失败一律映射到这个 400，**不得出现 422** |
| 唯一约束冲突 | 按约束名查表，见下方「唯一约束映射」 |
| 路由语义 | 与 Express 一致：`API_PREFIX` 下及排除路由（`/`、`/metrics`）的未匹配路由（含路径存在但方法不符）→ **404**，`message` 为 Nest 原文 `Cannot <METHOD> <originalUrl>`（含查询串，无翻译键，原文输出），`data: null`；Starlette 对方法不符默认 405，需覆盖为同样的 404 信封，且不带 `Allow` 头；尾斜杠不重定向（`redirect_slashes=False`），`/users` 与 `/users/` 都命中同一路由；路径匹配不区分大小写（Express 5 默认关闭 `case sensitive routing`，`/API/V1/Users` 与 `/api/v1/users` 命中同一路由）；其它路径（如 `/nope`、`POST /docs-json`）nestjs 返回 Express 默认 HTML 404，不属于契约，本服务统一返回同样的 JSON 信封，parity 不比对 |
| 鉴权声明 | 在实现侧逐路由声明，默认拒绝：未声明的路由启动时报错；五类：公开、JWT（`CurrentUser`）、可选 JWT（`OptionalUser`，如 papers controller 的 `OptionalJwtAuthGuard`）、refresh JWT（仅 `auth/refresh`，见 §6.3）、监控令牌（`healthCheck`、`/metrics`，`METRICS_TOKEN`，不走 JWT）；是否经过权限守卫（RBAC）是独立维度，见 §6.7；OpenAPI 中的 `security` 只用于契约测试交叉核对 |
| 语言 | 见 §4.2 |
| Request ID | 入站 `X-Request-Id` 匹配 `^[\w.:-]{1,128}$` 则沿用，否则生成 UUID；写日志、审计，并回写响应头 |
| CORS | 生产按 `FRONTEND_DOMAIN` 白名单，`credentials: true`，允许方法 `GET/POST/PUT/PATCH/DELETE/OPTIONS`、允许头 `Content-Type`、`Authorization`、`Accept-Language`；非生产反射来源与请求头；两种环境 `exposedHeaders` 均为`X-Request-Id`、`Retry-After`、`X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset` |
| 安全头 | 与 nestjs helmet 配置输出相同的头集合（CSP、`X-Content-Type-Options`、`Referrer-Policy`、CORP cross-origin、HSTS 等，HSTS 在所有环境都发送） |
| 请求体 | JSON 上限 256kb，超限 413 `{code: 413, message: "request entity too large", data: null}`（body-parser 原文，不翻译）；JSON 非法或顶层为原始值（`null`、字符串等）→ 400、`data: null`，`message` 为解析器错误文本，由各实现决定，parity 只比对 `code` 与 `data`；契约只约束 `application/json`，其它 content-type（nestjs 另接受 urlencoded 表单）不属于契约，本服务按空对象校验 |
| 限流 | 规则同 nestjs（`THROTTLE_ENABLED`，默认 100 次 / 60s，鉴权路由按用户、否则按 IP，IPv6 按子网；客户端 IP 按 `TRUST_PROXY` 代理层数从 `X-Forwarded-For` 取）；未被限流的响应（含之后的 401 / 422 等）带 `X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset`；429 只带 `Retry-After`、不带 `X-RateLimit-*`；`THROTTLE_ENABLED=false` 时不发任何限流头；路由级规则见 §4.6；算法见 §5 第 4 项 |
| 字段命名 | camelCase（Pydantic `alias_generator=to_camel`） |

**错误翻译规则**（照抄 nestjs `src/common/filters/all-exceptions.filter.ts`，翻译文件为 `src/i18n/{en,zh}/common.json`；「尝试翻译」指 `common.<key>` 存在则用译文，否则保留原值）：

| 异常 | `message` | `data` |
|---|---|---|
| 429 限流 | `common.tooManyRequests` | `null` |
| `BusinessException` | `common.<errorKey>` | `{...details, errorKey}`，不翻译 |
| 其它 HTTP 异常，带自定义 `message`（如 `ForbiddenException('Missing permissions: …')`） | 尝试翻译 `message`，无对应键时原文输出 | `errors` 字段，无则 `null` |
| 其它 HTTP 异常，无自定义 `message`，`error` 不是默认描述（如 `throwNotFoundError('sessionNotFound')`） | 尝试翻译 `error`（→ `Session not found` / `会话不存在`） | 同上 |
| 其它 HTTP 异常，无 `error` / `message` 或为默认描述 | 状态码默认消息 | 同上 |
| 唯一约束冲突 | 映射表有 `error` 时尝试翻译它，否则状态码默认消息 | 映射表的 `errors` |
| 未知异常 | `common.internalServerError` | `null` |

非 `BusinessException` 的 HTTP 异常与唯一约束冲突，`data` 中**顶层字符串值**尝试翻译（`{email: 'notFound'}` 输出为 `{email: 'Not found'}` / `{email: '资源不存在'}`），数字等非字符串值与嵌套 map 原样输出。下文 §6 中写作 `{field: 'key'}` 的错误均指翻译前的键。

**唯一约束映射**（照抄 nestjs `src/common/utils/exceptions.util.ts` 的 `UNIQUE_CONSTRAINT_RESPONSES`，PG 错误码 `23505`）：

| 约束名 | HTTP | `data`（翻译前） | `message` |
|---|---|---|---|
| `UQ_e12875dfb3b1d92d7d7c5377e22`（user.email） | 422 | `{email: 'emailAlreadyExists'}` | 默认 |
| `user_phone_key` | 422 | `{phone: 'phoneAlreadyExists'}` | 默认 |
| `user_wechatOpenId_key` | 422 | `{wechatOpenId: 'wechatOpenIdAlreadyExists'}` | 默认 |
| `user_alipayOpenId_key` | 422 | `{alipayOpenId: 'alipayOpenIdAlreadyExists'}` | 默认 |
| `REL_75e2be4ce11d447ef43be0e374`（user.photo） | 422 | `{photo: 'alreadyExists'}` | 默认 |
| `UQ_favorite_user_target` | 409 | `null` | `common.alreadyFavorited` |
| `UQ_question_subjectId_contentHash` | 409 | `null` | `common.duplicateQuestion`（本期不做，表项照抄） |
| `UQ_order_orderNo` | 500 | `null` | 默认（服务端生成值冲突） |
| `UQ_share_shareCode` | 500 | `null` | 默认 |
| 未登记的约束 | 409 | `null` | 默认 |

### 4.2 语言头：统一使用 `Accept-Language`

| | Accept-Language | x-custom-lang |
|---|---|---|
| 性质 | HTTP 标准头（RFC 9110），支持 q 值多语言优先级 | nestjs-boilerplate 自定义头 |
| 浏览器 | 自动携带用户系统语言，首次访问即可用 | 必须前端手动设置 |
| 缓存 / CDN | 可用标准 `Vary: Accept-Language`（本契约不发送，见下） | 需自定义 Vary |
| CORS | 属于安全列表头，不触发额外预检 | 自定义头，跨域时触发预检 |

**所有模板统一使用 `Accept-Language`，不再使用 `x-custom-lang`。** 后端按 q 值匹配已支持语言，匹配不到回落 `APP_FALLBACK_LANGUAGE`。nestjs 响应不带 `Content-Language`，也不带 `Vary: Accept-Language`（只有 CORS 中间件加的 `Vary: Origin`），本服务同样不发送这两项；`Content-Language`、`Vary` 不属于契约，parity 比对时排除。OpenAPI 全局参数 `Accept-Language` 已在基线中声明。

配套仓库已同步：

| 仓库 | 调整 |
|---|---|
| nestjs-template | i18n 解析器改为 `AcceptLanguageResolver`（`zh-CN` 等区域标签宽松匹配到 `zh`）；Swagger 全局头参数、CORS 允许头改为 `Accept-Language`；去掉 `APP_HEADER_LANGUAGE` |
| uniapp-template | 请求头改为 `Accept-Language`；Orval 生成代码不含该头，无需重新生成 |
| vite-vue3-temp | 已在发 `Accept-Language`，无需改动 |

### 4.3 OpenAPI（Orval 依赖）

前端仓库（vite-vue3-temp、uniapp-template）提交了 Orval 生成的客户端，并按名字直接引用考试业务的类型和函数（如 `CreatePaperDto`、`AdminQuestionDto`、`generatePaper`、`getPracticeSessions`）。如果 `/docs-json` 只含本服务实现的子集，前端重新生成后这些名字消失，`vue-tsc` 直接失败。因此：

- **`/docs-json` 输出 nestjs 完整基线**：返回 `openapi/nestjs-baseline.json` 原文，**逐字节一致**（`application/json`，直接读文件字节，不经 FastAPI / orjson 重新序列化）；包含本期不做的考试业务接口。UI：`/docs`（Scalar 静态页，指向 `/docs-json`）。`SWAGGER_ENABLED` 控制，生产默认关闭，与 nestjs 相同
- **基线导出条件**：nestjs 在非生产环境、`FILE_DRIVER=local` 下导出（nestjs 按驱动注册不同的上传 controller，只有 local 版带 `operationId: uploadFile` 且为 `multipart/form-data`），导出条件和提交号记入 `openapi/SOURCE`
- **未实现的基线接口**：路由表中不存在，请求返回与 nestjs 未知路由相同的 404 信封（`Cannot <METHOD> <originalUrl>`，见 §4.1「路由语义」）；完整清单由契约测试生成并写入 `docs/contract.md`，与实际不符时测试失败
- **`make openapi-sync`**：在 nestjs-template 固定提交上运行 nestjs 侧导出脚本，一次产出并一起提交：
  - `openapi/nestjs-baseline.json`：`/docs-json` 原文
  - `openapi/validation.json`：遍历 class-validator `getMetadataStorage()` 与 class-transformer 元数据，输出 `DTO → 字段 → [有序约束（类型、参数、自定义 message、each、groups）] + [转换（lowerCase、trim、md5Password…）] + 嵌套 / 数组类型`。nestjs 未启用 swagger CLI 插件，基线 schema 不含任何约束，校验行为只能从这份文件获得；Python、Go、Java 三个后端共用同一份
  - `openapi/SOURCE`：仓库、分支、提交号、导出环境
  - `openapi/nest_handlers.json`：operationId → nestjs `Controller-handler` 映射，限流键（与 nestjs 相同的哈希输入）用；由 `scripts/gen_nest_handlers.py` 从 nestjs controller 源码与基线生成，`uploadFile` 随 `FILE_DRIVER` 变化不入表；运行时从仓库根的 `openapi/` 读取，镜像须包含该目录
  - `db/nestjs-schema.sql`：nestjs 迁移后的表结构快照（`scripts/schema_snapshot.py`），迁移测试比对用
  - `tests/contract/fixtures/validation-cases.json`：由 `validation.json` 生成的校验用例及 nestjs ValidationPipe 计算的期望结果
- **sync 工具可在三个后端仓库原样运行**：`scripts/openapi-sync.sh`、`scripts/{gen_validation_cases,schema_snapshot,gen_nest_handlers}.py`、`scripts/nestjs/{validation-cases.ts,nestjs.compose.yaml}` 只依赖 Docker、git 与 uv，Python 脚本用 PEP 723 内联依赖（`# /// script`），`openapi-sync.sh` 以 `uv run --script` 调用，不依赖本仓库的 uv 项目环境；路径一律相对仓库根，只读写 `openapi/`、`db/`、`tests/contract/fixtures/`；在只含这几个目录与 `scripts/` 的空目录中即可运行（`NESTJS_DIR` 指向 nestjs-template 仓库）
- **校验对齐**：Pydantic 字段约束、`BeforeValidator`（对应 `@Transform`）与 422 消息格式化由 `validation.json` 生成或逐项核对：同字段全部失败约束都输出、按装饰器顺序 `", "` 拼接，消息用自定义 `message` 原文，否则用 class-validator 默认文案（`<field> must be an email` 等）；`tests/contract` 校验每个本期 DTO 的约束集合与 `validation.json` 一致，parity 比对 422 响应体
- **FastAPI 自身生成的 OpenAPI 只在契约测试内部使用**，不对外、不入库。`tests/contract` 逐项比对本期每个已实现 operation 与基线：path、method、operationId、tag、`security`、参数、请求体 content-type、schema 名与字段（请求与响应）、在已实现子集内的 paths 相对顺序；比对前归一化 OpenAPI 3.0 / 3.1 差异（`nullable: true` ↔ `anyOf [..., {type: null}]`）。白名单只有一项：s3-presigned 驱动的 `POST files/upload`（`application/json` 请求体 `{fileName, fileSize}`，见 §4.6），在 `docs/contract.md` 说明
- 鉴权以实现侧声明为准（§4.1「鉴权声明」），基线中的 `security` 只做交叉核对
- **内部 schema 与 nestjs DTO 名一致**，否则契约测试无法逐项比对：
  - `FastAPI(separate_input_output_schemas=False)`，避免生成 `XxxInput` / `XxxOutput` 两份 schema
  - Pydantic 模型类名直接用 nestjs DTO 名（`LoginResponseDto`、`QueryUserDto` 等）；泛型分页响应用 `model_config = {"title": ...}` 固定名字
  - 去掉 FastAPI 默认给每个路由加的 `422 HTTPValidationError` 响应及 `ValidationError` / `HTTPValidationError` schema
  - 查询参数模型（`Query` 依赖）展开为独立参数，与基线的参数列表一致
  - 文件上传使用 `multipart/form-data`，字段名 `file`
  - 响应 schema 为**未包装的 DTO**（前端 mutator 自己剥 `.data`）
- **router 注册顺序与 nestjs 一致**（AppModule 模块树先序遍历后各 controller 的声明顺序），契约测试校验已实现子集的相对顺序；对外顺序由基线文件保证
- 基线随 nestjs 变更通过 `make openapi-sync` 更新，§11.5 的定时 drift job 负责发现漂移

### 4.4 序列化细节

nestjs 用 class-transformer + `JSON.stringify` 输出，Pydantic 默认行为在以下几处不同，需显式对齐：

| 项 | nestjs 输出 | Pydantic 默认 | 对齐方式 |
|---|---|---|---|
| 时间 | `2026-11-10T12:00:00.000Z`（`toISOString`，毫秒、UTC、`Z`） | 微秒精度，可能带 `+00:00` | 自定义 `UtcDatetime` 类型：转 UTC、截到毫秒、以 `Z` 结尾 |
| 日期 | `birthday` 为 `YYYY-MM-DD` | 相同 | 无需处理 |
| 数据库时间列 | `timestamp`（无时区，TypeORM `CreateDateColumn` 默认） | — | 共库时保持 `timestamp` 并约定存 UTC；独立部署用 `timestamptz` |
| `undefined` 字段 | 不输出该键 | 输出 `null` | 按 nestjs **运行时**实际输出逐字段确认（nestjs DTO 常把可空字段在 spec 里声明为必填非空，spec 不可信）：运行时为 `null` 的输出 `null`，运行时缺省的键用 `exclude_unset` 不输出 |
| 分组字段 | `@Expose({ groups: ['me', 'admin'] })` 按视图隐藏字段，spec 中只有一个 DTO | 无分组概念 | 序列化时按视图（me / admin / public）选择字段集；路由在 OpenAPI 中声明的响应模型仍是 nestjs 的 DTO 名，视图只影响运行时输出 |
| 头像 | `photo.path` 为完整 URL | — | 序列化时按 `FILE_DRIVER` 拼接 `BACKEND_DOMAIN` 或 S3 地址 |
| 金额 | int（分） | — | 一律 int，不用 Decimal / float |

对照方法：同一份 seed 数据下，用 §11.1 的 parity job 对比两个后端的响应体；`tests/parity/fixtures/` 按视图各存一组 nestjs 实录响应（me：`auth/me`、登录响应中的 `user`；admin：`users`、`users/:id`；public：不带分组序列化的接口，逐个从本期路由中列出），逐键比对 `null` 与缺省。

### 4.5 分页

- 请求：`page`（≥1，默认 1）、`limit`（≥1，默认 10，路由上限 50 / 100），空串视为缺省；部分列表支持 JSON 字符串 `filters`、`sort`
- 标准响应：`{list, total, page, limit, totalPages}`
- 无限滚动（仅 `GET /users`）：`{data, hasNextPage}`

### 4.6 本期端点

**auth**（全部 200；`LoginResponse = {token, refreshToken, tokenExpires(ms), user}`）

| 方法 | 路径 | operationId | 鉴权 | 限流 |
|---|---|---|---|---|
| POST | auth/email/login | loginWithEmail | – | 5/60s |
| POST | auth/email/register | registerWithEmail | – | 默认 |
| POST | auth/email/confirm | confirmEmail | – | |
| POST | auth/email/confirm/new | confirmNewEmail | – | |
| POST | auth/forgot/password | forgotPassword | – | 3/300s |
| POST | auth/reset/password | resetPassword | – | 5/300s |
| GET | auth/me | getCurrentUser | JWT | |
| PUT | auth/me | updateCurrentUser | JWT | 10/60s |
| DELETE | auth/me | deleteCurrentUser | JWT | |
| POST | auth/refresh | refreshToken | refresh JWT（Bearer，无 body） | |
| POST | auth/logout | logout | JWT | |
| POST | auth/phone/send-code | sendSmsCode | – | 3/60s |
| POST | auth/phone/login | loginWithPhone | – | 5/60s |
| POST | auth/phone/sms-login | loginWithSms | – | 5/60s |
| POST | auth/phone/register | registerWithPhone | – | 3/60s |
| POST | auth/phone/reset-password | resetPasswordByPhone | – | 5/300s |
| PUT | auth/password | changePassword | JWT | 3/60s |
| POST | auth/wechat/login | loginWithWechat | – | 10/60s |
| POST | auth/wechat/phone-login | loginWithWechatPhone | – | 10/60s |
| POST | auth/alipay/login | loginWithAlipay | – | 10/60s |
| GET | auth/sessions | getMySessions | JWT | 返回 `[{id, userAgent, ip, createdAt, lastActiveAt, current}]` |
| DELETE | auth/sessions/:id | revokeMySession | JWT | 非本人会话 404 `sessionNotFound` |
| POST | auth/sessions/revoke-others | revokeOtherSessions | JWT | |

**users**（JWT + 权限，admin 视图）

| 方法 | 路径 | operationId | 权限 |
|---|---|---|---|
| POST | users | createUser | user:create |
| GET | users | getUsers（无限滚动） | user:list |
| GET | users/:id | getUser | user:read |
| PUT | users/:id | updateUser | user:update |
| DELETE | users/:id | deleteUser | user:delete |

**files**（JWT）：`POST files/upload?purpose=...`，**HTTP 201**（body `code` 200，见 §4.1）。形状随 `FILE_DRIVER` 变化，与 nestjs 按驱动注册不同 controller 一致：local / s3 为 `multipart/form-data` 字段 `file`，返回 `{file}`（基线按 local 导出，`operationId: uploadFile`）；s3-presigned 在同一路径上接收 `application/json` `{fileName, fileSize}`，返回 `{file, uploadSignedUrl}`，该变体不在基线中，契约测试白名单放行并记入 `docs/contract.md`。另有 `GET files/private/:id`（uuid，非法值按 nestjs 内置 `ParseUUIDPipe` 返回 400，`message` 为原文 `Validation failed (uuid is expected)`（无翻译键），`data: null`）、local 驱动 `GET files/:path`。

**health / metrics**

| 方法 | 路径 | operationId | 鉴权 |
|---|---|---|---|
| GET | /api/v1/health | healthCheck | 监控令牌（`MonitoringTokenGuard`，不走 JWT） |
| GET | /api/v1/health/live | livenessProbe | – |
| GET | /api/v1/health/ready | readinessProbe | – |
| GET | /api/health | simpleHealthCheck | – |
| GET | / | getAppInfo | – |
| GET | /metrics | 不进文档 | 监控令牌 |

**通用模块**：路由、operationId、权限、鉴权类型（含可选 JWT）照抄 nestjs 对应 controller。

---

## 5. 相对 nestjs-template 的改进（均不破坏现有契约）

| # | 项 | nestjs 现状 | 本模板 |
|---|---|---|---|
| 1 | 密码哈希 | bcrypt(md5)，cost 10 | **argon2id(md5)**（OWASP 首选）；登录时可验证旧 bcrypt 哈希并自动重哈希为 argon2id（pwdlib `verify_and_update`）。客户端仍传 MD5，前端无感 |
| 2 | 会话 | 已同步到 nestjs | 会话表可空列 `userAgent`（≤512）、`ip`、`lastActiveAt`（每会话最多 5 分钟写一次）；「我的设备」列表、下线指定设备、下线其他设备三个接口，见 §4.6 |
| 3 | 数据库迁移 | 每个应用容器启动时由 `scripts/startup.sh` 跑迁移（生产镜像不含 npm，直接 `node` 执行 TypeORM CLI） | 独立一次性 `migrate` 服务 / job，应用 `depends_on: service_completed_successfully`；多副本不会并发迁移 |
| 4 | 限流算法 | 固定窗口 | Redis Lua **GCRA**（平滑、无窗口边界突刺）；响应头名称与 nestjs 的 @nestjs/throttler 一致：`X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset`，429 时带 `Retry-After`；两种算法的剩余次数与重置时间不同，parity 只比对这些头存在且为整数；GCRA 状态存独立键（见 §9），共用 Redis 时 GCRA 后端（Python / Go / Java）共用 `:gcra` 键、共同计数，nestjs 用固定窗口键、单独计数；无 Redis 时按 worker 进程各自计数 |
| 5 | Request ID | 已同步到 nestjs（校验 + 回写） | 同 nestjs；日志另带 OTel `trace_id` / `span_id`，日志与链路可互相跳转 |
| 6 | OpenAPI | 运行时生成，无版本管理，spec 不含校验约束 | 不自行生成对外文档：`/docs-json` 输出入库的 nestjs 完整基线原文，校验元数据 `validation.json` 一并入库（`make openapi-sync`）；契约测试逐 operation 比对实现与基线，定时 drift job 发现 nestjs 漂移，见 §4.3、§11 |
| 7 | 新表主键 | uuid v4 | **UUIDv7**（Python 3.14+ 标准库 `uuid.uuid7()`，按时间有序，索引友好）；已有 int 主键（user 等）保持不变 |
| 8 | 事务 | 服务内手动管理 | 每请求一个 `AsyncSession`，依赖注入统一提交 / 回滚；需要跨请求的副作用（发邮件、投递任务）在提交成功后执行 |
| 9 | 优雅停机 | 依赖 Nest 默认 | lifespan 中关闭连接池、Redis、队列 broker；Granian 收到 SIGTERM 后等待在途请求完成 |
| 10 | 配置校验 | 运行时按模块校验 | 启动时一次性校验全部 Settings，缺必填项立即退出并列出全部缺失项 |
| 11 | 语言头 | 已同步到 nestjs（`AcceptLanguageResolver`） | 标准头 `Accept-Language`，见 §4.2 |
| 12 | 测试隔离 | e2e 连真实服务、串行执行 | 集成测试用 testcontainers 起 Postgres / Redis，可 `-n auto` 并行；普通用例在 SAVEPOINT 内执行后回滚；依赖真实提交的用例（refresh CAS 并发、提交后副作用、队列投递）标 `committed`，不走回滚模式，改用 `CREATE DATABASE … TEMPLATE` 从已迁移模板库克隆的独立库（或用例结束清表）；testcontainers 起不来时 CI 直接失败，不跳过 |

第 1 项与「两个后端共用同一个库」有冲突（nestjs 无法验证 argon2id）：共用期间设 `PASSWORD_HASHER=bcrypt`，独立部署时用默认 `argon2id`。第 2、5、11 项与限流响应头已在 nestjs 实现，属于共同契约。

---

## 6. 认证与权限

### 6.1 JWT

- access：`{id, role: {id}, sessionId, iat, exp}`，HS256，`AUTH_JWT_SECRET`，默认 15m
- refresh：`{sessionId, hash, iat, exp}`，`AUTH_REFRESH_SECRET`，默认 3650d
- forgot / confirm-email 令牌独立密钥（30m / 1d）；找回密码令牌内嵌密码指纹 `sha256(hash)[:16]`，改密后失效
- 生产环境四个密钥必须 ≥32 字符且互不相同

### 6.2 access 校验

只认 Bearer 头 → 必须有 `id`、`sessionId` → 查缓存 `jwt:session:{sessionId}`（30s，存 `{statusId, roleId}`），未命中查库：会话存在且属于该用户、用户状态 active / inactive → 以数据库角色覆盖令牌角色；同时节流更新 `lastActiveAt`（每会话每 5 分钟最多一次）。失败 401。

### 6.3 刷新轮换

- `POST /auth/refresh`，refresh JWT 放 Bearer 头
- 会话不存在或已无关联用户 → 401
- 每次轮换成功后把被换掉的旧 hash 写入 `session:rotated:{id}`，TTL 60s（nestjs `session.service.ts` 的 `ROTATED_HASH_GRACE_MS`）
- `session.hash != payload.hash`：若 `payload.hash` **等于** `session:rotated:{id}` 中存的旧 hash（`wasRecentlyRotatedFrom`），视为并发或重试，普通 401、不吊销；否则判为重放，吊销会话并 401
- hash 一致则生成新 hash，以 `UPDATE ... WHERE hash = old` 做 CAS，失败 401；返回新 access + refresh
- logout 软删会话并清缓存；改密 / 重置 / 换邮箱删除其它会话；03:00 物理删除软删超过 7 天的会话

### 6.4 密码

客户端传 MD5 hex（32 位，转小写）；服务端哈希见 §5 第 1 项；比较时同时尝试大写 MD5。

### 6.5 登录方式

| 方式 | 行为 |
|---|---|
| 邮箱 | 注册后 inactive 并发确认邮件；active / inactive 均可登录；错误 422 `{email:'notFound'}` / `{password:'incorrectPassword'}`（输出为译文，如 `{email:'Not found'}`，见 §4.1 错误翻译规则） |
| 手机 + 密码 | 错误 422 `{credentials:'incorrectPhoneOrPassword'}` |
| 短信登录 | 不自动注册，未知手机号 422 `{phone:'notFound'}` |
| 手机注册 | 直接 active 并返回令牌 |
| 微信 | `mini_app` → jscode2session，`app` → OAuth；按 openId 自动建号，唯一冲突时重查 |
| 微信手机号 | 绑定 openId + 手机，不合并账号（`phoneBoundToOtherWechat`），非大陆号拒绝（`phoneRegionNotSupported`） |
| 支付宝 | 按 alipayOpenId 自动建号 |

### 6.6 短信验证码

- `send-code` 预检：register 要求手机号不存在；login / reset_password / verify_identity 要求存在；change_phone 返回 `unsupportedCodeType`
- `SMS_CODE_LENGTH`（6）位数字，缓存 `sms:code:{type}:{phone}` = `{code, attempts, createdAt}`，TTL 300s
- 重发间隔 60s（检查 + 锁 `sms:send:{type}:{phone}`），失败 422 `{phone:'rateLimited'|'sendFailed', retryAfter}`（`phone` 输出译文，`retryAfter` 为数字原样输出）
- 最多 5 次尝试；成功或超限删除；失败 422 `{code:'invalidCode'}`
- 阿里云 Dysms；`SMS_MOCK_MODE=true` 只打日志

### 6.7 RBAC

- 角色 admin=1、user=2、editor=3、reviewer=4；状态 active=1、inactive=2
- 权限枚举、默认映射照抄 nestjs（枚举值不变）
- 表 `permission`、`role_permission`；数据库为空回落代码默认值；缓存 5 分钟
- `require_permissions(...)`（AND）、`require_any_permission(...)`（OR）；路由级覆盖类级
- 只有挂了权限守卫（对应 nestjs `PermissionsGuard`，`src/core/permissions/permissions.guard.ts`）却未声明任何权限的路由一律 403（message 原文 `No permission declared for this route`）；只要求 JWT 的路由（如 `auth/me`）不经过权限守卫，不受影响
- 缺权限时 403，message 为 nestjs 原文 `Missing permissions: a, b` / `Missing any of permissions: a, b`（无对应翻译键，按 §4.1 规则原文输出）

---

## 7. 数据模型

表名、列名与 nestjs 一致（camelCase 列名，ORM 属性 snake_case，通过 `mapped_column("firstName")` 映射），两个后端可指向同一个库。

本期表：user、role、status、session、permission、role_permission、file、audit_logs、config、region、notification、notification_setting、address、favorite、feedback、user_consent、account_cleanup、admin_alert。约束名（如 `user_phone_key`）照抄；软删 `deletedAt`；已删除用户释放唯一字段的部分索引同样实现。新约束遵循 `MetaData(naming_convention=...)`。

迁移：Alembic 初始迁移一次建出上述结构；CI 中 `upgrade head` → `alembic check`（模型与迁移无差异）→ `downgrade base` → `upgrade head`（验证可回滚）。

**共库时的迁移归属**：两套迁移工具（TypeORM migrations、Alembic）不能同时管理同一个库，否则会重复建表、互相覆盖。约定：

| 部署方式 | 表结构归属 | Python 侧做法 |
|---|---|---|
| 独立部署（默认） | Alembic | 正常 `upgrade head` |
| 与 nestjs 共库 | nestjs（TypeORM） | 设 `DB_SCHEMA_OWNER=external`：migrate 服务跳过，启动时只校验所需表 / 列存在；会话设备列已在 nestjs 迁移 `AddSessionClientInfo` 中 |

---

## 8. 基础设施

| 能力 | 实现 |
|---|---|
| 日志 | structlog；生产 JSON、开发彩色单行（`LOG_LEVEL`、`LOG_PRETTY_PRINT` 默认值同 nestjs）；请求日志照 nestjs 实际挂载的 pino-http autoLogging：消息 `METHOD url status`，字段 context=HTTP、`req{id,method,url,query,headers,remoteAddress}`、`res{statusCode}`、responseTime，`req.id` 为解析后的 Request ID；每条日志带 request_id、trace_id/span_id（有活动 span 时）、user_id（有 Bearer JWT 时）；脱敏 authorization、cookie、password/oldPassword/newPassword/code/phone/email、query 中的 hash/token；≥500 或异常 error、≥400 warn；worker / scheduler 进程同样初始化 |
| 指标 | `/metrics`，指标名、help、类型、标签、桶同 nestjs（http_requests_total、http_request_duration_seconds、http_active_connections、database_query_duration_seconds、cache_hits_total、cache_misses_total、queue_jobs_total、admin_alerts_total、admin_alerts_deduplicated_total、throttler_storage_fallback_total），path 标签为 Express 路由模板（未匹配 `<unmatched>`，客户端断开记 499）；标签 `app` 取 `APP_NAME`，未设置时为 `nestjs-app`（同 nestjs）；`METRICS_ENABLED=false` 时保留端点但不输出默认进程指标、不挂 HTTP 中间件；默认进程指标为 Python 的 `process_*` / `python_*`；多 worker 用 prometheus multiprocess 模式：API 与 Taskiq worker 须共享 `PROMETHEUS_MULTIPROC_DIR`，`python -m app` 启动前清空其中旧文件，`WEB_CONCURRENCY>1` 未设置时启动告警 |
| 链路 | OTel 自动埋点 FastAPI、SQLAlchemy、Redis、httpx，OTLP/HTTP 导出（`OTEL_ENABLED`，默认关闭）；Sentry（`SENTRY_DSN`，空则不启用） |
| 健康检查 | `/api/v1/health`（监控令牌）、`/health/live`、`/health/ready` 响应为 terminus 形状 `{status, info, error, details}` 外套信封，details 先 up 后 down，带 `Cache-Control: no-cache, no-store, must-revalidate`；失败 503 `{code: 503, message: "SERVICE_UNAVAILABLE", data: null}`；检查项：内存（Python 无 V8 堆，用进程 RSS 对比同一 300MB 阈值）、磁盘、数据库 ping、连接池、Redis、微信 / 支付宝 API 可达（未配置时 up 并注明 skipped）；`/api/health` 为简单检查 |
| 缓存 | Redis 命名空间 `cache`（键 `cache::<key>`、值 `{"value","expires"}`，与 Keyv 互通），默认 TTL 60s；Redis 未启用时用进程内存 |
| 分布式锁 | 键 `lock:<key>`，`SET NX PX` + Lua 释放，单条命令 1s 超时；Redis 未启用或出错时退回进程内锁 |
| 队列 | Taskiq + Redis Stream（`taskiq:mail` / `taskiq:notification` / `taskiq:account-cleanup` / `taskiq:scheduler`，不裁剪，与 BullMQ 不互通）；默认重试 3 次、指数退避 1s / 2s（BullMQ 公式 `delay * 2^(n-1)`，在任务内等待）；worker 崩溃后未确认消息由 XAUTOCLAIM（10 分钟空闲）回收；无 Redis 时进程内执行（仅限开发 / 测试，生产强制 `REDIS_ENABLED=true`，与 nestjs 一致）；API 启动时 Redis 不可达只记日志不阻塞，`app worker` 则直接退出 |
| 邮件 | aiosmtplib + Jinja2，模板由 nestjs Handlebars 原样转换，渲染结果（标题、text、html）与 nestjs 逐字节一致；设置了 `MAIL_HOST` 时提交后投递到 mail 队列，入队时记录当前请求解析出的语言；入队 2s 超时或失败时本进程后台直接发送；未设置 `MAIL_HOST` 时只记日志（开发 / 测试），生产要求 `MAIL_HOST` / `MAIL_DEFAULT_EMAIL` / `MAIL_DEFAULT_NAME`；邮件带 `Date`、`Message-ID` 头。与 nestjs 的差异：nestjs 在请求内同步发送，SMTP 失败返回 500；本服务异步发送，SMTP 失败不影响响应 |
| 定时任务 | Taskiq scheduler 独立进程，以 `lock:scheduler:<name>` 加锁防止多实例并发执行：UTC 03:00 物理删除软删超过 7 天的会话；UTC 04:00 软删已读超过 30 天的通知（nestjs 未设时区，按容器本地时间，容器为 UTC）；每小时第 25 分钟（Asia/Shanghai）账号清理补偿：软删超过 30 分钟且无完成记录的用户，每批 200 个重新入队 |
| 审计 | 路由声明 `audit=Auditable(...)` 写 `audit_logs`：字段、脱敏规则、entityId 取值同 nestjs；`userId` 只取鉴权路由的当前用户；请求成功后在独立事务写入，失败只记日志 |
| 告警 | `AlertReporter.raise_(...)` 写 `admin_alert`，`INSERT … ON CONFLICT DO NOTHING` 按 dedupKey 去重，最多给 50 个管理员各写一条站内通知；限流存储降级时告警（`throttler-storage-fallback`，同 nestjs） |
| 账号清理 | 删会话 → 软删 → 事务提交后入队 `account-cleanup`（5 次重试，10s 起指数退避）；Redis 去重键 `taskiq:account-cleanup:user-{id}`（24 小时，任务结束即删除）防止重复入队，执行时持有 `lock:account-cleanup:user-{id}` 防止并发清理；清理器按 order 执行（users 900 解除头像关联，files 1000 删除文件；M4 模块的清理器随模块加入），单个失败不中断其余，全部成功写 `account_cleanup` 完成记录，最终失败告警 `accountCleanupFailed:{userId}`；管理端 `POST admin/users/:id/cleanup`（`adminRetriggerAccountCleanup`）重新触发 |
| 文件 | `FILE_DRIVER`（`local` / `s3` / `s3-presigned`），按用途限制大小与类型，私有文件经接口鉴权；local 驱动 `GET files/:path` 只返回 PUBLIC 文件；s3 / s3-presigned 的文件 URL 为 3600 秒预签名 GET；上传的错误形状与 multer / busboy 一致 |
| 进程 | api（Granian）、worker（`app worker`，Taskiq）、scheduler（`app scheduler`，Taskiq）、migrate（一次性）四个容器 |

---

## 9. 配置（环境变量）

变量名与 nestjs `env-example-relational` 一致，差异：

| nestjs | Python |
|---|---|
| `NODE_ENV` | `APP_ENV`（development / test / production） |
| `DATABASE_TYPE`、`DATABASE_SYNCHRONIZE` | 不需要 |
| — | `PASSWORD_HASHER`、`PASSWORD_HASH_CONCURRENCY`、`WORKER_DATABASE_MAX_CONNECTIONS`、`DB_SCHEMA_OWNER`、`OTEL_ENABLED`、`OTEL_EXPORTER_OTLP_ENDPOINT`、`SENTRY_DSN`、`WEB_CONCURRENCY`、`PROMETHEUS_MULTIPROC_DIR` |

与 nestjs 同名同义、契约相关的变量（必须支持，默认值与 nestjs 一致）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `APP_PORT` | `3000` | 监听端口；前端 Orval target、dev 环境、manage.sh、HEALTHCHECK 均按 3000 |
| `API_PREFIX` | `api` | 全局前缀，`/`、`/metrics` 除外 |
| `TRUST_PROXY` | `0` | 反向代理**层数**（整数，非布尔），限流按 `X-Forwarded-For` 倒数第 N 跳取客户端 IP |
| `REDIS_ENABLED` | 未设置视为 `false`（只认字符串 `true`）；`env-example-relational` 为 `true` | 关闭时缓存 / 限流回落内存、队列进程内执行；生产强制 `true` |
| `THROTTLE_ENABLED` / `THROTTLE_TTL` / `THROTTLE_LIMIT` | 未设置视为 `true`，`env-example-relational` 为 `false` / `60` / `100` | 限流开关与默认规则 |
| `METRICS_ENABLED` / `METRICS_PATH` / `METRICS_TOKEN` | `true` / `metrics` / 空 | 指标路径与监控令牌；生产未配令牌时拒绝访问 |
| `SWAGGER_ENABLED` | 非生产 `true`、生产 `false` | 控制 `/docs`、`/docs-json` |
| `APP_FALLBACK_LANGUAGE` | `en` | 语言回落 |
| `METRICS_TOKEN` 长度 | 设置时至少 16 位 | 同 nestjs 校验 |
| `LOG_LEVEL` / `LOG_PRETTY_PRINT` | 生产 `info` / `false`，其它环境 `debug` / `true` | 日志级别与输出格式；`LOG_PRETTY_PRINT` 只认字符串 `true` |
| `FILE_DRIVER` | `local`（nestjs 要求必填） | `local` / `s3` / `s3-presigned` |
| `FILE_IMAGE_MAX_SIZE` / `FILE_PRIVATE_IMAGE_MAX_SIZE` / `FILE_CONTENT_IMAGE_MAX_SIZE` / `FILE_AUDIO_MAX_SIZE` / `FILE_IMPORT_MAX_SIZE` / `FILE_EXPORT_MAX_SIZE` | 5MB / 10MB / 5MB / 20MB / 50MB / 20MB | 按上传用途限制大小 |
| `ACCESS_KEY_ID` / `SECRET_ACCESS_KEY` / `AWS_DEFAULT_S3_BUCKET` / `AWS_S3_REGION` / `AWS_S3_ENDPOINT` / `AWS_S3_FORCE_PATH_STYLE` | 空 / 空 / 空 / 空 / 空 / `true` | S3 系驱动的凭据、桶、区域与地址；s3 / s3-presigned 缺凭据、桶或区域时启动失败 |
| `WECHAT_APP_ID` / `WECHAT_APP_SECRET` / `ALIPAY_APP_ID` | 空 | 健康检查的微信 / 支付宝 API 可达检查，未配置时跳过（M4 登录集成同用这些变量） |
| `MAIL_HOST` / `MAIL_PORT` / `MAIL_USER` / `MAIL_PASSWORD` / `MAIL_IGNORE_TLS` / `MAIL_SECURE` / `MAIL_REQUIRE_TLS` / `MAIL_DEFAULT_EMAIL` / `MAIL_DEFAULT_NAME` | `MAIL_PORT` 587，其余空 / `false` | SMTP，TLS 选项语义同 nodemailer；nestjs 始终必填 `MAIL_HOST` / `MAIL_DEFAULT_EMAIL` / `MAIL_DEFAULT_NAME`，本服务仅生产必填，非生产未设置时只记日志 |
| `DATABASE_SSL_ENABLED` / `DATABASE_REJECT_UNAUTHORIZED` / `DATABASE_CA` / `DATABASE_KEY` / `DATABASE_CERT` | `false` / `true` / 空 | 语义同 node-postgres 的 ssl 选项，CA / KEY / CERT 为 PEM 文本 |

`DATABASE_MAX_CONNECTIONS` 同名但默认值与 nestjs 不同：默认 `80`（nestjs 为 `100`），语义为全部 API 进程合计上限，按 `WEB_CONCURRENCY` 均分到每个进程。worker、scheduler 进程各自按 `WORKER_DATABASE_MAX_CONNECTIONS`（默认 `10`）建连接池，不按 `WEB_CONCURRENCY` 均分；`app migrate`、`app seed` 为部署时运行的一次性进程，各用 1 个连接。部署时须满足 API + worker + scheduler ≤ Postgres `max_connections`，默认值为 80 + 10 + 10 ≤ 100。

nestjs 未使用 `REDIS_KEY_PREFIX`（键前缀写死在代码里，如 `throttle:`、`lock:`、`session:rotated:`），本服务同样不引入，Redis 键名与 nestjs 逐字一致，便于共用实例。例外：限流算法不同（§5 第 4 项），GCRA 状态存在独立键 `throttle:<sha256>:default:gcra`（哈希输入与 nestjs 相同），共用实例时 GCRA 后端（Python / Go / Java）共用该键、共同计数，nestjs 用固定窗口键、单独计数，两者互不干扰；队列用 Taskiq stream（`taskiq:*`），与 BullMQ 的 `bull:*` 不互通。

GCRA 键 `throttle:<sha256>:default:gcra` 的值格式为 Python、Go、Java 三个后端共用 Redis 时的约定：

- 值为 TAT（理论到达时间），单位毫秒，以 Redis `TIME` 为时间基准，十进制字符串存储；过期时间为 `PX ceil(TAT - now)`
- 读取方须兼容任意小数位（含无小数）
- Python 在 Lua 中用 `tostring` 写入（`%.14g`，当前毫秒时间戳下最多 1 位小数）；Go、Java 内部用整数微秒计算，写入时转成最多 3 位小数的毫秒字符串

Settings 按分组拆成多个 `BaseSettings`，敏感字段用 `SecretStr`，启动时一次性校验。

---

## 10. 工程配置（业界当前最佳实践）

### 10.1 项目配置

| 项 | 做法 |
|---|---|
| 布局 | src 布局，`[build-system]` 用 `uv_build` |
| 依赖分组 | PEP 735 `[dependency-groups]`：`dev`（包含 lint、type、test）；生产镜像 `--no-dev` |
| 解释器 | `.python-version`；CI、Docker、本地统一由 uv 读取 |
| Ruff | `target-version = "py315"`；规则集 E、W、F、I、B、UP、SIM、C4、RUF、ASYNC、S（安全）、PT（pytest）、TC（类型导入）、DTZ（时区）、PERF、LOG、G、N、PL（部分）、**FAST**（FastAPI 专用规则）；`ruff format` 统一格式 |
| mypy | `strict = true` + `pydantic.mypy` 插件；ty 同步跑，稳定后替换 mypy |
| pytest | `--strict-markers --strict-config`、`asyncio_mode = "auto"`、`xfail_strict`、`filterwarnings = error`；markers：unit / integration / e2e |
| 覆盖率 | branch 覆盖，`fail_under = 85` |
| 编辑器 | `.editorconfig`；`.vscode/settings.json`（Ruff 格式化、mypy）、`extensions.json` 推荐插件 |
| 任务入口 | 根目录 `Makefile`：`dev`、`check`、`fmt`、`test`、`test-e2e`、`test-contract`、`test-parity`、`migrate`、`openapi-sync`、`infra`、`ci`、`clean`；CI 与本地调用同一组目标 |

### 10.2 Git 钩子（prek）

| 阶段 | 内容 |
|---|---|
| pre-commit | ruff check --fix、ruff format、typos、`uv lock --check`、gitleaks（密钥泄露）、check-yaml / toml、end-of-file、trailing-whitespace |
| commit-msg | conventional commits，类型集合与 nestjs commitlint 一致，header ≤72 |
| pre-push | `make check`（lint + type + unit） |

### 10.3 Docker

- 多阶段：`ghcr.io/astral-sh/uv:0.13` 拷贝 uv → builder（`python:3.15-slim`，`uv sync --frozen --no-dev --no-install-project` 先装依赖层，再装项目；`UV_COMPILE_BYTECODE=1`、`UV_LINK_MODE=copy`、BuildKit cache mount）→ runtime（`python:3.15-slim`，非 root 用户，只拷 `.venv`；`HEALTHCHECK` 用 `python -c` + `urllib` 调 `http://localhost:${APP_PORT:-3000}/api/v1/health/live`，slim 镜像不额外装 curl）；另有 `development` 目标
- 构建：buildx 多架构（amd64 / arm64），生成 SBOM 与 provenance attestation
- `.dockerignore` 白名单写法
- hadolint 检查 Dockerfile
- compose：`docker/` 下与 nestjs 同名同职责（base、postgres、redis、dev、test、ci、ecs），邮件服务用 Mailpit；dev 用 `develop.watch` 热同步

### 10.4 依赖与安全

- Renovate：uv、Docker 镜像、GitHub Actions 统一管理；patch / minor 分组自动合并（CI 通过前提下），major 单独 PR；`lockFileMaintenance` 每周
- 漏洞扫描（与 nestjs 的 check.yml 一致）：`pip-audit`（生产依赖 high 及以上）、Trivy（镜像 HIGH / CRITICAL 且有修复版本）、gitleaks（只扫本次 push / PR 引入的提交）、zizmor（workflow 安全，`.github/zizmor.yml` 配置忽略项，门槛 medium）、actionlint（workflow 语法）
- GitHub Actions 全部按 commit SHA 固定版本并注释版本号，由 Renovate 更新

### 10.5 文档

README（中文，结构参照 nestjs-template）、CLAUDE.md / AGENTS.md（架构决策 + 禁止事项）、`docs/`：architecture、auth、database、deploy、testing、cli、contract（基线中未实现的 operation 清单，由契约测试生成；s3-presigned 上传变体等白名单说明；与 nestjs 的其它差异）、benchmark（压测基线）。

---

## 11. CI / CD（参照 nestjs-template）

### 11.1 check.yml

触发：push / PR 到 main、master、`release/**`，手动。`permissions: contents: read`；concurrency 取消旧运行；每个 job `timeout-minutes: 15`；`astral-sh/setup-uv` 启用缓存；checkout 一律 `persist-credentials: false`。job 划分与 nestjs 的 check.yml 对应（lint / type-check / test / build / migrations / workflow-lint / security / image-scan / e2e），另加 Python 侧的 contract、parity；nestjs 漂移检查为独立的定时 workflow（§11.5）。

| job | 内容 | 依赖 |
|---|---|---|
| lint | ruff check、ruff format --check、typos、hadolint | – |
| workflow-lint | actionlint（docker 镜像）、zizmor-action（min-severity medium） | – |
| type-check | mypy；ty（`continue-on-error`） | – |
| test | pytest unit + integration（testcontainers），`-n auto`，上传覆盖率；`committed` 用例使用克隆的独立库（§5 第 12 项）；容器起不来即失败，不允许 skip | – |
| migrations | postgres service；upgrade → check → downgrade → upgrade | – |
| contract | `/docs-json` 与 `openapi/nestjs-baseline.json` 逐字节一致；FastAPI 内部 OpenAPI 的每个已实现 operation 与基线逐项一致（§4.3）；本期 DTO 约束与 `validation.json` 一致；未实现清单与 `docs/contract.md` 一致；`openapi/SOURCE` 的提交号与 parity 使用的 nestjs 版本一致 | – |
| security | pip-audit、gitleaks（docker 镜像，`--log-opts` 为本次提交范围） | – |
| build | 构建应用 / 镜像检查 | – |
| image-scan | 构建生产镜像 + Trivy；失败单独标红，不阻塞 e2e | – |
| e2e | compose（base + postgres + redis + ci）起完整服务跑 e2e，失败上传日志 | lint、type-check、test、build、migrations、workflow-lint、security |
| parity | 用 `tests/parity` 黑盒用例同时打本服务和 nestjs-template（两者用同一份 seed），比对状态码与响应体（忽略 id、时间、令牌等易变字段）及契约相关响应头；另外直接复用 nestjs 的 `test/user/auth.e2e-spec.ts` 打本服务；表结构比对 | build、migrations |

parity job 是「前端无改动切换」最直接的证据。说明：

- nestjs 来源：`xingyunqi-tech/nestjs-template` 为公开仓库，parity 使用其 GHCR 镜像或固定提交 `02fdb4d` 的 checkout 构建（与 `openapi/SOURCE` 一致），不跟随分支 HEAD
- nestjs 的 e2e 中只有 `test/user/auth.e2e-spec.ts`（11 个用例）通过 `APP_URL` 以 HTTP 黑盒方式访问，可直接指向本服务复用；其余目录用 `createTestApp` 在进程内启动 Nest，无法复用
- 因此本仓库自建 `tests/parity`：pytest + httpx，目标地址参数化，覆盖本期全部端点的正常与错误路径；同一套用例对 nestjs 也必须通过，用来证明用例本身写的是 nestjs 的真实行为
- 前提：seed 账号一致（`admin@example.com`、`john.doe@example.com`，密码 `Secret00`）、测试用密钥一致、端口可配置（默认均为 3000，并行起两个服务时映射不同宿主端口）
- 限流：`env-example-relational` 中 `THROTTLE_ENABLED=false`，parity 环境两侧都显式设为 `true`；限流头只比对存在性与整数值（GCRA 与固定窗口数值不同）
- 响应头：比对 `X-Request-Id`、`X-RateLimit-*`、`Retry-After`、`Content-Type`；`Content-Language`、`Vary` 不在契约内，排除
- 覆盖：每种错误形状（含 422 校验体、非法 `:id` 400、方法不符 404、尾斜杠、upload 201）；序列化按视图的 fixtures 见 §4.4
- 表结构：nestjs 迁移跑完后，用 `information_schema` / `pg_catalog` 查询（表、列、类型、可空、默认值、约束名、索引）与 `alembic upgrade head` 后的库比对；结果快照为 `db/nestjs-schema.sql`，随 `make openapi-sync` 一并更新

### 11.2 deploy.yml

与 nestjs 一致：手动触发，输入 deploy、target（aliyun / tencent / all）；多架构镜像推 GHCR（+ 腾讯 TCR），带 SBOM / provenance；矩阵部署 `aliyun-production` / `tencent-production`；scp compose + manage.sh；SSH 拉镜像（3 次重试）→ 执行 migrate 服务 → `up -d --remove-orphans` → 轮询 `/api/v1/health/ready` 30 次 → 保留最新 3 个镜像；notify `timeout-minutes: 5`。

### 11.3 cleanup-registry.yml

与 nestjs 一致：每周一 03:00（北京时间）+ 手动 dry_run + workflow_call；GHCR 保留 2 个带标签镜像并删无标签；TCR 未配密钥时跳过。

### 11.4 deploy/manage.sh

命令集同 nestjs（start / stop / restart / remove / status / logs / health / cleanup / help），容器名 `fastapi-api`，健康检查 `http://localhost:${APP_PORT:-3000}/api/v1/health/ready`；`start` 先跑 migrate 服务。

### 11.5 nestjs-drift.yml

定时（每天）+ 手动触发。checkout nestjs-template `release/exam-prep` 分支 HEAD，运行 `scripts/openapi-sync.sh`（`uv run --script` 调用 PEP 723 脚本，不需要安装本仓库依赖）导出 `openapi/`（基线、`validation.json`、`SOURCE`、`nest_handlers.json`）、`db/nestjs-schema.sql` 与 `tests/contract/fixtures/validation-cases.json`，与本仓库入库版本比对；有差异时 job 失败并在摘要中输出 diff 与 nestjs 新提交号，由人工执行 `make openapi-sync` 并补实现。不改动本仓库、不提交。

---

## 12. 实施阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| **M0 清理与骨架** | 删除 frontend/、packages/、bun、Traefik、FastAPI Cloud、无关 workflow；`origin` 改名 `upstream`；定 Python 版本；src 布局；依赖升级；§10 工程配置（Ruff、mypy、pytest、prek、Makefile、Renovate、编辑器配置）；从 nestjs `02fdb4d`（`FILE_DRIVER=local`）导出 `openapi/nestjs-baseline.json` 与 `openapi/SOURCE` 入库 | `make check`、`make test` 全绿；`/docs-json` 返回 `openapi/nestjs-baseline.json` 原文；默认端口 3000 |
| **M1 全局契约** | `make openapi-sync`（基线、`validation.json`、SOURCE）；响应包装、异常处理与翻译规则、唯一约束映射、校验错误（按 `validation.json`）、i18n、Request ID、CORS、安全头、分页、ApiRoute；Express 路由语义（方法不符 404、尾斜杠）；契约测试框架 | 每种错误形状有测试；`/docs-json` 与基线逐字节一致；成功响应除 upload（201）外均为 HTTP 200；非法 `:id` 为 400；方法不符为 404；与 nestjs 同请求响应体一致 |
| **M2 数据层与认证核心** | async SQLAlchemy、Alembic 初始迁移、核心表、seed；邮箱注册 / 登录 / 确认 / 找回、refresh 轮换、logout、me、改密、注销、会话管理接口；RBAC；users 接口 | vite-vue3-temp、uniapp-template 对本服务 `/docs-json` 重新生成 Orval 客户端零 diff、`vue-tsc` 通过；vite-vue3-temp 不改代码指向本服务，可完成登录、刷新令牌、获取用户信息、退出（后台里的考试业务页面请求本期不做的接口，会收到 404 并报错，属预期，不在验收范围）；nestjs `test/user/auth.e2e-spec.ts` 指向本服务全部通过；`tests/parity` 认证与用户部分对两个后端均通过 |
| **M3 基础设施** | 缓存 / 锁、限流、日志、指标、链路、健康检查、Sentry、Taskiq 队列与定时、审计、告警、账号清理（含 `adminRetriggerAccountCleanup`）、邮件、文件三驱动 | 集成测试覆盖：限流（响应头、429、路由级规则、按用户 / IP、`TRUST_PROXY`、Redis 键名、多实例共享、Redis 故障回落与告警）；健康检查（三个端点、503、监控令牌）；`/metrics`（指标名集合按 nestjs 清单断言、标签、桶、令牌）；上传（三种驱动，s3 用 MinIO；错误形状、私有文件权限、local 下载）；锁竞争与回退；队列投递与重试（Redis Stream 与进程内）；邮件经 SMTP 实际送达且中英模板与 nestjs 渲染结果逐字节一致；审计、告警去重、账号清理全链路与补偿、定时任务清理条件；`tests/parity` 的 files 对照通过；nestjs `test/user/auth.e2e-spec.ts` 仍全部通过 |
| **M4 扩展登录与通用模块** | 短信、手机号、微信、支付宝登录；8 个通用模块；代码生成器 | uniapp-template 指向本服务可手机号登录；契约测试本期已实现 operation 零差异（白名单仅 s3-presigned 上传） |
| **M5 交付** | Dockerfile、compose、manage.sh、四个 workflow（check、deploy、cleanup-registry、nestjs-drift）、Renovate、安全扫描、README / CLAUDE.md / docs | CI 全绿（含 e2e、contract、parity、security）；`make ci` 本地通过；k6 压测登录、刷新、`auth/me`、列表四个接口，结果记录到 `docs/benchmark.md`，与 nestjs 同环境对比 |

每个阶段结束停在工作区，review 后再决定是否提交。

---

## 13. 待决策

| # | 问题 | 建议 |
|---|---|---|
| 1 | Python 3.15 还是 3.14 | 3.15，M0 验证 wheel 后定 |
| 2 | 是否与 nestjs 共用表结构（camelCase 列名） | 共用，便于切换与对照测试；实际共库时 `DB_SCHEMA_OWNER=external`（表结构归 nestjs，§7）、`PASSWORD_HASHER=bcrypt` |
| 3 | 默认密码哈希（`PASSWORD_HASHER`） | 默认 argon2id；与 nestjs 共库时配置为 bcrypt |
| 4 | 是否保留六边形分层 | core 保留；通用模块用 service + repository 两层，减少样板 |
| 5 | orders、payments、shares 是否进第二期 | 作为可选模块进第二期 |
| 6 | 仓库远程 | `origin` 改 `upstream` 仅作参考，不再合并上游 |
| 7 | ty 何时替代 mypy | ty 发布 1.0 或连续两个月零误报后替换 |
| 8 | 是否把 API 契约抽成独立文档 | 抽出 §4、§6、§7、§9 中的契约部分为与语言无关的《后端模板 API 契约》，放在 template 目录下；nestjs、Python、Go、Java 模板都以它为准；机器可读部分即 `openapi/` 下的基线与 `validation.json`，不在文档中重复 |
| 9 | class-validator 元数据导出脚本（`validation.json`）放在哪里 | 放 nestjs-template（`scripts/export-validation.ts`，直接读取运行时 class-validator / class-transformer 元数据），由 nestjs 维护者随 DTO 变更更新；Python / Go / Java 三个仓库的 `make openapi-sync` 调用它并提交同一份产物；需在 nestjs 侧另开任务，脚本落地前 `validation.json` 从 nestjs DTO 源码半自动生成并人工核对，格式保持一致 |
| 10 | parity 中 nestjs 的来源 | 优先用 GHCR 镜像（与 `openapi/SOURCE` 提交号对应的标签）；镜像不可用时固定提交 `02fdb4d` checkout 构建 |
| 11 | 是否复现 Express 的弱 ETag 与 304（nestjs 未关闭 Express 默认的 `etag: weak`，GET 响应带 `ETag`，请求带匹配的 `If-None-Match` 时返回 304 空体） | 不纳入契约，parity 忽略 ETag 头并且不发送 If-None-Match |

---

## 14. 风险

| 风险 | 影响 | 应对 |
|---|---|---|
| Python 3.15 原生扩展 wheel 不全 | M0 无法安装依赖 | 降到 3.14，`requires-python` 已兼容 |
| `/docs-json` 不是 nestjs 完整基线 | 前端重新生成后考试业务类型 / 函数（`CreatePaperDto`、`generatePaper` 等）消失，`vue-tsc` 失败 | `/docs-json` 直接输出基线原文，contract job 校验逐字节一致 |
| 已实现接口与基线不一致（schema 名、字段、operationId、content-type） | 前端生成的类型与实际响应不符 | FastAPI 内部 OpenAPI 与基线逐 operation 比对，白名单仅 s3-presigned 上传 |
| `validation.json` 缺项或 Pydantic 约束语义与 class-validator 不等价 | 本服务比 nestjs 宽松或更严，422 消息不同 | 契约测试校验约束集合；parity 覆盖每个本期 DTO 的必填 / 格式错误并比对 422 响应体 |
| 序列化细节差异（时间格式、null / 缺省、分组字段） | 前端显示或判断异常 | §4.4 对齐表 + parity 按视图（me / admin / public）的 fixtures |
| 测试只在回滚模式下运行 | refresh CAS 并发、提交后副作用等问题测不出 | `committed` 用例用克隆库（§5 第 12 项）；testcontainers 失败即 CI 失败 |
| 共库时两套迁移冲突 | 表结构被覆盖或重复创建 | §7 迁移归属约定，`DB_SCHEMA_OWNER=external` |
| nestjs-template 后续接口变更 | 两个后端契约漂移 | 基线固定在 `02fdb4d`；§11.5 定时 drift job 对比 release 分支 HEAD，发现差异后 `make openapi-sync` 并补实现 |
| ty 仍是预览版 | 类型检查结果不稳定 | mypy 作为门禁，ty 不阻塞 CI |

---

## 附：版本核对来源

- PyPI（2026-11-10）：fastapi 0.162.4、starlette 1.10.0、pydantic 2.14.3、pydantic-settings 2.16.2、sqlalchemy 2.1.8、asyncpg 0.32.0、redis 8.1.0、taskiq 0.13.0、taskiq-redis 1.2.4、pyjwt 2.15.1、pwdlib 0.3.1、structlog 26.1.0、prometheus-client 0.26.0、opentelemetry-instrumentation-fastapi 0.66b2、sentry-sdk 2.74.0、granian 2.8.7、orjson 3.13.0、typer 0.29.0、pytest 9.2.0、pytest-asyncio 1.4.1、pytest-xdist 3.8.0、coverage 7.17.2、testcontainers 4.15.0、ruff 0.17.4、ty 0.0.100、uv 0.13.7、aiosmtplib 5.1.3、aioboto3 15.5.0、httpx 0.28.1（1.0 为 dev）、arq 0.28.0（2026-04-16 后无发布）
- 上游 `uv.lock`：sqlmodel 0.0.47、alembic 1.20.0、mypy 2.3.1、sqlalchemy 2.0.51、fastapi 0.141.1
- endoflife.date：Python 3.15.2（2026-10-09）、PostgreSQL 18.6 / 17.11（18.7、17.12 尚未发布）、Redis 8.10.3
- nestjs-template 契约（`release/exam-prep` `02fdb4d`）：`src/common/interceptors/response.interceptor.ts`、`src/common/filters/all-exceptions.filter.ts`、`src/common/utils/exceptions.util.ts`、`src/common/validation-options.ts`、`src/common/pipes/parse-id.pipe.ts`、`src/common/guards/*`、`src/i18n/{en,zh}/common.json`、`src/core/auth/*`、`src/core/session/session.service.ts`、`src/core/permissions/*`、`src/modules/files/infrastructure/uploader/*`、`src/config/app.config.ts`、`src/main.ts`、`env-example-relational`、`scripts/startup.sh`、`test/user/auth.e2e-spec.ts`、`test/platform/platform-foundation.e2e-spec.ts`、`.github/workflows/*`
