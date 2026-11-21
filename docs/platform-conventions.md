# 跨语言平台约定

适用仓库：fastapi-temp（Python）、go-template（Go）、springboot-template（Java）。三个仓库中本文件逐字节相同，修改时三处同步。

本文只约定**对外可观察**的行为：HTTP 接口格式、令牌、请求头、日志字段、共享 Redis 键。实现方式、目录结构、数据库表结构由各仓库按本语言惯用写法决定。目标是：

- 任一前端按同一套约定对接任一后端，只需按该后端的 OpenAPI 文档重新生成客户端；
- 三个模板可以作为同一个 monorepo 里的不同服务共存，令牌互认、日志与链路可关联。

版本：v1（2026-11-17）。

---

## 1. 路径与文档

| 项 | 约定 |
|---|---|
| 业务前缀 | `/api/v1`；版本只在路径中体现 |
| 运维端点 | `GET /health/live`（进程存活）、`GET /health/ready`（本服务必需依赖就绪，失败 503）、`GET /metrics`（Prometheus 拉取模式下提供，`METRICS_TOKEN` Bearer 或独立端口；改为 OTLP 推送时可不提供），均不带前缀、不包信封；`ready` 不检查第三方服务与其他服务 |
| OpenAPI | 3.1，由各服务从代码或本仓库维护的 spec 生成；运行时 `GET /openapi.json`，UI `GET /docs`（Scalar）；`DOCS_ENABLED` 控制，生产默认关闭 |
| 文档入库 | `openapi/openapi.json` 入库，CI 重新导出并比对，有差异即失败 |
| operationId | 全局唯一，lowerCamelCase，动词开头（`listUsers`、`createUser`） |
| 文档内容 | 描述真实响应（含 §2 信封）；错误响应引用公共 `ErrorResponse` schema，422 引用在其基础上细化 `data` 的 `ValidationErrorResponse`；鉴权用 `bearerAuth` 安全方案 |
| 路由 | 区分大小写；末尾斜杠不重定向，`/users/` 与 `/users` 是不同路径；路径存在但方法不符返回 405 `common.methodNotAllowed` 并带 `Allow` 头 |
| 字段命名 | JSON 一律 lowerCamelCase |

## 2. 响应格式

### 2.1 成功

```json
{ "code": 0, "message": "ok", "data": <payload | null> }
```

- HTTP 状态码按语义：查询 / 更新 200，创建 201，异步受理 202；业务接口不使用 204，无返回值时 `data: null`；CORS 预检由框架处理，不受此限
- `code` 为 0 表示成功
- 运维端点、JWKS、文件下载、SSE、第三方回调不包信封

### 2.2 错误

```json
{
  "code": 40102,
  "message": "邮箱或密码错误",
  "errorKey": "auth.invalidCredentials",
  "data": null,
  "requestId": "019a8f3c-6b1e-7d2a-9c4f-1e2d3c4b5a69"
}
```

| 字段 | 说明 |
|---|---|
| HTTP 状态 | 取 §2.4 中该 `errorKey` 的状态 |
| `code` | 业务错误码，整数，`HTTP 状态 × 100 + 序号`，全表唯一；同一 `errorKey` 在三个服务中取值相同 |
| `message` | 按 `Accept-Language` 翻译后的文本，只供展示 |
| `errorKey` | 稳定的机器可读键，`<域>.<名称>`，前端分支判断只用它 |
| `data` | 附加信息，没有则 `null` |
| `requestId` | 与响应头 `X-Request-Id` 相同 |

- 401 响应带 `WWW-Authenticate: Bearer`；请求携带了令牌但无效或过期时追加 `error="invalid_token"`（RFC 6750）
- 未捕获异常一律 500 `common.internalError`，响应中不含异常信息

### 2.3 校验错误

HTTP 422，`errorKey: "common.validationFailed"`，`data.errors` 为数组，每个失败字段至少一项；同一字段有多个失败约束时，可以只报第一个；请求体解码阶段的类型错误可以只报第一个字段：

```json
{
  "code": 42200,
  "message": "数据验证失败",
  "errorKey": "common.validationFailed",
  "data": {
    "errors": [
      { "field": "email", "rule": "email", "message": "邮箱格式不正确" },
      { "field": "items[0].qty", "rule": "min", "message": "不能小于 1", "params": { "min": 1 } }
    ]
  },
  "requestId": "..."
}
```

- `field`：JSON 路径（点号 + 下标），请求体字段直接写名字，查询 / 路径参数写 `query.page`、`path.id`
- `rule`：取值 `required`、`type`、`email`、`url`、`uuid`、`min`、`max`、`minLength`、`maxLength`、`minItems`、`maxItems`、`pattern`、`enum`、`format`、`exists`（引用的资源不存在）、`contains`（数组缺少必需元素，`params.value` 给出该元素），以及各服务自定义的 lowerCamelCase 名；`params` 可选，给出约束参数
- 字段类型不符（如字符串传数字）、枚举值非法也属于 422，`rule` 分别为 `type`、`enum`
- 查询参数中非法的 `sort` 字段、`cursor`：422，`field` 为 `query.sort` / `query.cursor`
- 字符串长度（`minLength`、`maxLength`）按 Unicode 码点计数
- 未知字段：忽略
- 请求体不是合法 JSON：400 `common.malformedJson`；`Content-Type` 不受支持：415 `common.unsupportedMediaType`；超过 `BODY_LIMIT`：413 `common.payloadTooLarge`（文件上传接口按 §11.5 的上限，超出返回 413 `file.tooLarge`）

### 2.4 错误码

| errorKey | HTTP | code | 场景 |
|---|---|---|---|
| `common.badRequest` | 400 | 40000 | 其他请求错误 |
| `common.malformedJson` | 400 | 40001 | 请求体不是合法 JSON |
| `common.unauthorized` | 401 | 40100 | 未携带令牌、令牌无效或会话已吊销，客户端应重新登录 |
| `auth.tokenExpired` | 401 | 40101 | access 已过期，客户端应刷新 |
| `auth.invalidCredentials` | 401 | 40102 | 邮箱或密码错误 |
| `auth.refreshTokenReused` | 401 | 40103 | 已轮换的 refresh 在宽限期外再次使用，会话已吊销 |
| `auth.refreshTokenInvalid` | 401 | 40104 | refresh 不存在、过期或会话已吊销，客户端应重新登录 |
| `common.forbidden` | 403 | 40300 | 缺少权限 |
| `auth.accountDisabled` | 403 | 40301 | 账号被禁用 |
| `auth.emailNotVerified` | 403 | 40302 | 业务接口要求已验证邮箱 |
| `auth.reauthRequired` | 403 | 40303 | 敏感操作要求近期登录（§5.4） |
| `common.notFound` | 404 | 40400 | 资源不存在 |
| `auth.providerDisabled` | 404 | 40401 | 第三方登录 provider 未启用 |
| `auth.sessionNotFound` | 404 | 40402 | 会话不存在或不属于当前用户 |
| `user.notFound` | 404 | 40403 | 用户不存在 |
| `common.methodNotAllowed` | 405 | 40500 | 路径存在但方法不符 |
| `common.conflict` | 409 | 40900 | 其他冲突 |
| `user.emailTaken` | 409 | 40901 | 邮箱已被占用 |
| `user.phoneTaken` | 409 | 40902 | 手机号已被占用 |
| `common.idempotencyInProgress` | 409 | 40903 | 同一 `Idempotency-Key` 的首次请求仍在处理 |
| `role.lastAdmin` | 409 | 40904 | 操作会使系统中没有可用的 `admin` |
| `common.payloadTooLarge` | 413 | 41300 | 请求体超过 `BODY_LIMIT` |
| `file.tooLarge` | 413 | 41301 | 上传文件超过该用途的大小上限 |
| `common.unsupportedMediaType` | 415 | 41500 | `Content-Type` 不受支持 |
| `common.validationFailed` | 422 | 42200 | 校验失败，见 §2.3 |
| `auth.verificationCodeInvalid` | 422 | 42201 | 短信验证码错误、过期或超过尝试次数 |
| `auth.verificationTokenInvalid` | 422 | 42202 | 邮箱验证、找回密码令牌无效或过期 |
| `auth.passwordIncorrect` | 422 | 42203 | 改密时当前密码错误 |
| `file.typeNotAllowed` | 422 | 42204 | 文件类型不在该用途的白名单 |
| `common.idempotencyKeyMismatch` | 422 | 42205 | 同一 `Idempotency-Key` 用于不同请求 |
| `auth.providerCodeInvalid` | 422 | 42206 | 第三方登录或微信手机号的 `code` 无效或过期 |
| `common.tooManyRequests` | 429 | 42900 | 触发限流 |
| `auth.sendTooFrequent` | 429 | 42901 | 验证码发送冷却或当日上限 |
| `auth.verificationLocked` | 429 | 42902 | 该手机号验证失败次数过多，暂时锁定 |
| `common.internalError` | 500 | 50000 | 未预期错误 |
| `common.badGateway` | 502 | 50200 | 第三方服务（微信、支付宝、短信、邮件）调用失败 |
| `common.serviceUnavailable` | 503 | 50300 | 依赖不可用 |

新增条目按 `HTTP 状态 × 100 + 序号` 取下一个未用值，三个仓库同步登记到本表；只在单个服务内使用的错误也必须先登记。

## 3. 分页

| 方式 | 请求 | 响应 `data` |
|---|---|---|
| 页码 | `page`（≥1，默认 1）、`pageSize`（1–100，默认 20） | `{ "items": [], "total": 0, "page": 1, "pageSize": 20 }` |
| 游标 | `cursor`（不透明字符串，首页省略）、`limit`（1–100，默认 20） | `{ "items": [], "nextCursor": "..." \| null }` |

排序：`sort=field,-field2`（`-` 降序），可排序字段由各接口白名单限定。

## 4. 数据类型

| 类型 | 约定 |
|---|---|
| 主键 | UUIDv7，JSON 为小写带连字符字符串 |
| 时间 | 输出 RFC 3339，UTC，毫秒精度，`Z` 结尾：`2026-11-17T08:00:00.000Z`；输入接受任意时区偏移的 RFC 3339；数据库用 `timestamptz` |
| 日期 | `YYYY-MM-DD` |
| 金额 | 整数，最小货币单位（分） |
| 枚举 | 小写 snake_case 字符串，不用数字 |
| 空值 | 可空字段输出 `null`，不省略键 |
| 邮箱 | 去首尾空白后转小写存储与比较，唯一性不区分大小写 |
| 手机号 | E.164 存储与输出（`+8613800000000`）；输入接受 E.164 或 11 位中国大陆号码（补 `+86`） |

## 5. 认证

### 5.1 令牌

| 令牌 | 形式 | 有效期 | 说明 |
|---|---|---|---|
| access | JWT（JWS），头部 `alg: EdDSA`、`kid` 必填，密钥为 Ed25519 | 15 分钟（`AUTH_ACCESS_TTL`） | 任一服务用 JWKS 公钥验证 |
| refresh | 不透明随机串（32 字节，base64url） | 30 天（`AUTH_REFRESH_TTL`） | 服务端只存 SHA-256；每次刷新轮换 |

access claims：

| claim | 值 |
|---|---|
| `iss` | `AUTH_ISSUER` |
| `aud` | `AUTH_AUDIENCE`，单个字符串 |
| `sub` | 用户 id（UUIDv7） |
| `sid` | 会话 id |
| `roles` | 角色代码数组，如 `["admin"]` |
| `email_verified` | 签发时邮箱是否已验证（布尔）；验证邮箱后刷新或重新登录才更新 |
| `auth_time` | 本会话完成登录的时间（Unix 秒），刷新时原样沿用 |
| `iat` / `exp` / `jti` | 标准含义 |

密钥与验证：

- 签发服务用 `AUTH_SIGNING_KEY`（Ed25519 私钥 PEM）签名；`AUTH_VERIFY_KEYS`（PEM 公钥列表，可空）为轮换中仍需接受的旧公钥
- `GET /.well-known/jwks.json` 发布当前公钥与 `AUTH_VERIFY_KEYS`，每个 JWK 为 `kty: OKP`、`crv: Ed25519`，带 `kid`（RFC 7638 指纹，SHA-256，base64url）、`alg: EdDSA`、`use: sig`；响应 `Cache-Control: max-age=300`
- 公开接口不校验 `Authorization` 头，携带过期或无效令牌也按匿名处理
- 验证方只接受头部 `alg: EdDSA`、且 `kid` 指向 `kty: OKP`、`crv: Ed25519` 的 JWK；校验 `iss`、`aud`、`exp`，时钟偏差容忍 30 秒；遇到未知 `kid` 时刷新 JWKS，两次刷新间隔不少于 30 秒

吊销：

- 签发服务在以下时机写入 Redis `auth:revoked:sid:{sid}`，TTL 为 `AUTH_ACCESS_TTL` + 60 秒：登出、吊销单个会话、吊销其他会话、改密（其他会话）、重置密码（全部会话）、禁用用户、删除用户、修改用户角色（该用户全部会话）、检测到 refresh 重用
- `EdDSA` 已被 RFC 9864 标为弃用名称（新名称 `Ed25519`）；因主流库支持不齐，签发继续使用 `EdDSA`，切换时验证方先同时接受两个名称
- 所有服务验证 access 后必须查询该键，命中返回 401 `common.unauthorized`；Redis 出错时返回 503 `common.serviceUnavailable`；未配置 `REDIS_URL` 时只在本进程内生效

### 5.2 刷新与会话

- `POST /api/v1/auth/refresh`，body `{ "refreshToken": "..." }`，返回新的 access + refresh
- 会话自登录起最长 `AUTH_REFRESH_TTL`，轮换不延长；到期后必须重新登录
- 已轮换的 refresh 哈希保留到所属会话过期，用于识别重用
- 轮换后旧 refresh 进入 30 秒宽限期，宽限期内重复使用返回同一对新令牌；宽限期外再次使用视为泄露，吊销整个会话并返回 401 `auth.refreshTokenReused`
- 宽限期内的令牌对由签发服务暂存，必须加密，不得明文落盘；轮换与暂存必须原子，并发刷新只产生一对新令牌
- refresh 不存在、过期或会话已吊销：401 `auth.refreshTokenInvalid`
- access 过期返回 401 `auth.tokenExpired`；其他无效情况返回 401 `common.unauthorized`

### 5.3 登录响应

```json
{ "accessToken": "...", "refreshToken": "...", "expiresIn": 900, "tokenType": "Bearer", "user": { ... } }
```

`user` 为 §11.4 的 `User`。

### 5.4 密码与敏感操作

- 客户端经 HTTPS 发送明文，服务端不接受客户端预哈希
- 长度 8–128，不强制字符组成；违反时 422，`rule` 为 `minLength` / `maxLength`
- argon2id，`m=19456 KiB, t=2, p=1`，PHC 字符串存储；参数变化时登录成功后重哈希
- 账号不存在时也对固定的假哈希执行一次校验，使响应时间与账号存在时一致
- 敏感操作要求近期登录：access 的 `auth_time` 距今不超过 10 分钟，否则 403 `auth.reauthRequired`；适用于 `deleteMe`，以及未设置过密码时的 `changePassword`

### 5.5 角色与权限

- 角色集合与平台权限码的映射由代码固定，三个服务相同，运行时不可修改
- 角色：`admin`（全部权限）、`user`（无管理权限）；每个用户都有 `user`，新用户只有 `user`
- 平台权限码：`user:read`、`user:write`、`role:read`、`role:write`、`audit:read`，只授予 `admin`
- 各服务可追加私有权限码（`<资源>:<动作>`），在本服务代码中声明授予哪些角色，只在本服务生效；`admin` 在每个服务都拥有该服务的全部权限码
- `listRoles`、`listPermissions` 只反映签发服务自己的权限码
- 修改用户角色后吊销该用户全部会话（§5.1），新角色在重新登录后生效

## 6. 请求头

| 头 | 方向 | 约定 |
|---|---|---|
| `Authorization` | 入 | `Bearer <access>` |
| `Accept-Language` | 入 | 支持 `zh`、`en`，按 q 值匹配，区域标签宽松匹配（`zh-CN` → `zh`），默认 `APP_DEFAULT_LOCALE`（`zh`） |
| `X-Request-Id` | 入 / 出 | 入站匹配 `^[A-Za-z0-9._:-]{1,128}$` 则沿用，否则生成 UUIDv7；响应总是回写 |
| `traceparent` / `tracestate` | 入 / 出 | W3C Trace Context，OpenTelemetry 透传 |
| `Idempotency-Key` | 入 | 可选，见下 |
| `X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset` | 出 | 见 §7 |
| `Retry-After` | 出 | 429、503 时给出秒数 |

`Idempotency-Key`：

- 只用于在 OpenAPI 中声明支持的写接口；作用域为 `(用户或 IP, 方法, 实际请求路径, 键)`，保存 24 小时
- 首次请求得到 2xx 或 4xx 后，相同作用域、相同请求体的重复请求返回首次的状态码与响应体；5xx 不保存，释放该键
- 首次请求未完成时返回 409 `common.idempotencyInProgress`；请求体不同返回 422 `common.idempotencyKeyMismatch`

CORS：

- 生产按 `CORS_ORIGINS` 白名单，非生产允许任意来源；不使用 cookie，`Access-Control-Allow-Credentials` 不发送
- 允许头：`Authorization`、`Content-Type`、`Accept-Language`、`X-Request-Id`、`Idempotency-Key`、`traceparent`、`tracestate`
- 暴露头：`X-Request-Id`、`Retry-After`、`X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset`

## 7. 限流

- 算法 GCRA；规则由速率与突发组成，突发为可连续通过的请求数
- 键：`rl:{规则名}:{主体}`，值为 TAT（Unix 毫秒十进制整数字符串），`PX` 为 `TAT - now`；`now` 在 Redis Lua 脚本内用 `TIME` 取得
- 主体：`u:{sub}`（用户）、`ip:{IP}`（IPv6 取 /64）、`s:{sid}`（会话）、`a:{SHA-256(规范化的邮箱或手机号) 前 16 位十六进制}`（账号）
- 客户端 IP 按 `TRUSTED_PROXIES` 从 `X-Forwarded-For` 右起取
- 响应头：`X-RateLimit-Limit` 为突发值，`X-RateLimit-Remaining` 为当前还可连续通过的次数，`X-RateLimit-Reset` 为恢复到满额的秒数；同一请求命中多条规则时取剩余最少的一条；429 只带 `Retry-After`
- 共享规则（§11.6）三个服务参数相同、共同计数；服务私有规则名以 `{OTEL_SERVICE_NAME}-` 开头，键仍为 `rl:{规则名}:{主体}`
- `REDIS_URL` 未配置时用进程内实现；已配置但 Redis 出错时，共享规则拒绝请求（503 `common.serviceUnavailable`），私有规则放行

## 8. 日志与可观测性

- 生产输出 JSON 行日志，字段：`ts`（RFC 3339 毫秒 UTC）、`level`（`debug` / `info` / `warn` / `error`）、`msg`、`service`、`env`、`request_id`、`trace_id`、`span_id`，访问日志另加 `method`、`route`、`status`、`duration_ms`、`user_id`
- 不记录 `Authorization` 头、令牌、密码、验证码、一次性令牌；邮箱、手机号掩码输出（`a***@example.com`、`+86138****0000`）
- `service` 与 OpenTelemetry `service.name` 取 `OTEL_SERVICE_NAME`，必填
- OpenTelemetry 使用标准 `OTEL_*` 环境变量，HTTP 指标使用稳定语义约定
- Prometheus 中 HTTP 服务端指标名为 `http_server_request_duration_seconds`（直方图），标签至少含 `http_request_method`、`http_route`、`http_response_status_code`
- 错误上报 Sentry（`SENTRY_DSN`），事件带 `request_id`

## 9. 公共环境变量

| 变量 | 说明 |
|---|---|
| `APP_ENV` | `development` / `test` / `production` |
| `APP_PORT` | 默认 3000 |
| `APP_DEFAULT_LOCALE` | 默认 `zh` |
| `APP_FRONTEND_URL` | 邮件中链接的前端地址，签发服务生产必填 |
| `DATABASE_URL` | libpq URI：`postgresql://user:pass@host:5432/db?sslmode=disable`，各服务自行转换为驱动格式 |
| `DATABASE_SCHEMA` | 本服务独占的 schema，默认 `public`；迁移负责创建 |
| `REDIS_URL` | 未设置时缓存、锁、限流、吊销用进程内实现；生产必填 |
| `BODY_LIMIT` | 非上传接口的请求体上限，默认 `1MiB` |
| `AUTH_ISSUER` / `AUTH_AUDIENCE` | 见 §5.1 |
| `AUTH_SIGNING_KEY` / `AUTH_VERIFY_KEYS` | 签发服务配置；设置了 `AUTH_SIGNING_KEY` 即为签发服务 |
| `AUTH_JWKS_URL` | 验证服务配置；与 `AUTH_SIGNING_KEY` 同时设置时启动失败 |
| `AUTH_ACCESS_TTL` / `AUTH_REFRESH_TTL` | 时长，`15m`、`720h` 格式 |
| `CORS_ORIGINS` | 逗号分隔 |
| `TRUSTED_PROXIES` | 可信代理层数或 CIDR 列表 |
| `METRICS_TOKEN` | `/metrics` 鉴权 |
| `DOCS_ENABLED` | 是否暴露 `/openapi.json` 与 `/docs` |
| `LOG_LEVEL` / `LOG_FORMAT` | `LOG_FORMAT` 为 `json` / `text` |
| `OTEL_SERVICE_NAME` | 必填，见 §8 |
| `SENTRY_DSN`、其他 `OTEL_*` | 见 §8 |

各服务特有变量自行命名，不与上表冲突。

## 10. 同一 monorepo 内共存

- 每个服务独占一个 PostgreSQL schema（`DATABASE_SCHEMA`），只迁移、只写自己的 schema；不建跨 schema 外键，引用用户只存 `sub`
- 只有一个服务承担签发（用户、会话、登录），其余服务设 `AUTH_JWKS_URL` 只做验证
- Redis 键：`auth:revoked:sid:` 键（§5.1）与 `rl:` 开头的键（§7）按原格式使用；其余键一律带 `{OTEL_SERVICE_NAME}:` 前缀
- 签发服务删除用户后发布 `user.deleted` 事件（CloudEvents 1.0 JSON，`data` 为 `{ "userId": "..." }`），验证服务据此清理自己的数据；事件的传输方式与所用的键、主题名由 monorepo 配置，不受上一条前缀规则约束，消费者组以 `OTEL_SERVICE_NAME` 命名
- 服务间同步调用建议使用 Connect / gRPC，proto 放在 monorepo 的 `contracts/proto/`

## 11. 身份与文件接口

签发服务必须提供 §11.1–§11.3 与 §11.5；验证服务提供文件上传能力时按 §11.5，不提供时不挂载；路径、方法、operationId、请求与响应字段三个仓库相同。路径均在 `/api/v1` 下（JWKS 除外）。

### 11.1 认证

| 方法 | 路径 | operationId | 鉴权 | 请求 | 成功 |
|---|---|---|---|---|---|
| GET | `/.well-known/jwks.json` | 不进文档 | 公开 | — | 200 JWKS 原文 |
| POST | `/auth/email/register` | `registerWithEmail` | 公开 | `{email, password, displayName?}` | 201 登录响应；入队验证邮件 |
| POST | `/auth/email/login` | `loginWithEmail` | 公开 | `{email, password}` | 200 登录响应 |
| POST | `/auth/email/verify` | `verifyEmail` | 公开 | `{token}` | 200 `data: null` |
| POST | `/auth/email/verify/resend` | `resendVerificationEmail` | JWT | — | 202 `data: null` |
| POST | `/auth/password/forgot` | `forgotPassword` | 公开 | `{email}` | 202 `data: null`，邮箱不存在同样返回 202 |
| POST | `/auth/password/reset` | `resetPassword` | 公开 | `{token, password}` | 200 `data: null`；吊销该用户全部会话 |
| POST | `/auth/sms/send` | `sendSmsCode` | `login` 公开，`bind_phone` 需 JWT | `{phone, scene}`，`scene` 为 `login` / `bind_phone` | 202 `data: null` |
| POST | `/auth/sms/login` | `loginWithSms` | 公开 | `{phone, code}` | 200 登录响应；未注册手机号自动注册 |
| GET | `/auth/providers` | `listAuthProviders` | 公开 | — | 200 `[{id}]`，只含已启用的 provider |
| POST | `/auth/providers/{provider}/login` | `loginWithProvider` | 公开 | `{code}` | 200 登录响应；首次登录自动注册 |
| POST | `/auth/refresh` | `refreshToken` | 公开 | `{refreshToken}` | 200 登录响应 |
| POST | `/auth/logout` | `logout` | JWT | — | 200 `data: null`；吊销当前会话 |

- 邮箱或密码错误统一 401 `auth.invalidCredentials`，不区分账号是否存在；凭据正确但账号被禁用返回 403 `auth.accountDisabled`；`registerWithEmail` 对已注册邮箱返回 409 `user.emailTaken`，由限流缓解枚举
- 邮箱未验证可以登录；需要已验证邮箱的业务接口返回 403 `auth.emailNotVerified`
- 验证码：6 位数字，有效 5 分钟，与 `(phone, scene)` 绑定，单个验证码最多尝试 5 次；同一手机号发送冷却 60 秒、每日最多 10 次，超出 429 `auth.sendTooFrequent`；同一手机号 1 小时内累计失败 10 次后锁定 1 小时，期间返回 429 `auth.verificationLocked`
- 一次性令牌（邮箱验证 24 小时、找回密码 30 分钟）服务端只存哈希，使用一次即失效；同类新令牌签发后旧令牌失效
- 第三方 `code` 无效或过期：422 `auth.providerCodeInvalid`；第三方服务调用失败：502 `common.badGateway`
- `provider` 取值：`wechat_miniapp`（`code` 为 `wx.login` 结果）、`wechat_app`（OAuth `code`）、`alipay`（`auth_code`）；路径参数按普通字符串接收，未知或未启用返回 404 `auth.providerDisabled`
- 第三方身份按 `(provider, subject)` 唯一；微信身份同时保存 `openid` 与 `unionid`（可得时），按任一命中视为同一身份，取得 `unionid` 后回填，`wechat_miniapp` 与 `wechat_app` 经 `unionid` 共用同一账号；手机号相同也不自动合并账号

### 11.2 当前用户

| 方法 | 路径 | operationId | 请求 | 成功 |
|---|---|---|---|---|
| GET | `/me` | `getMe` | — | 200 `User` |
| PATCH | `/me` | `updateMe` | `{displayName?, avatarFileId?}` | 200 `User`；`avatarFileId` 须为本人上传、用途为 `avatar` 的文件，否则 422（`field: avatarFileId`，`rule: exists`） |
| DELETE | `/me` | `deleteMe` | — | 202 `data: null`；要求近期登录（§5.4）；软删用户、吊销全部会话、异步清理数据 |
| PUT | `/me/password` | `changePassword` | `{currentPassword?, newPassword}` | 200 `data: null`；吊销其他会话。已设置密码时 `currentPassword` 必填（缺失 422 `required`，错误 422 `auth.passwordIncorrect`）；未设置过密码时省略，并要求近期登录 |
| PUT | `/me/phone` | `bindPhone` | `{phone, code}`（`scene=bind_phone` 的验证码） | 200 `User`；手机号已属于其他账号 409 `user.phoneTaken`，不合并账号 |
| PUT | `/me/phone/wechat` | `bindPhoneWithWechat` | `{code}`（`getPhoneNumber` 结果） | 200 `User`；`wechat_miniapp` 未启用 404 `auth.providerDisabled`；手机号冲突同 `bindPhone` |
| GET | `/me/sessions` | `listMySessions` | — | 200 `[Session]` |
| DELETE | `/me/sessions/{id}` | `revokeMySession` | — | 200 `data: null`；不属于当前用户 404 `auth.sessionNotFound` |
| POST | `/me/sessions/revoke-others` | `revokeOtherSessions` | — | 200 `data: null` |

全部需要 JWT。

### 11.3 管理

| 方法 | 路径 | operationId | 权限 | 请求 / 成功 |
|---|---|---|---|---|
| GET | `/users` | `listUsers` | `user:read` | 页码分页；筛选 `keyword`（邮箱、手机号、昵称）、`status`、`role`；排序 `createdAt`、`email`，默认 `-createdAt` |
| POST | `/users` | `createUser` | `user:write` | `{email?, phone?, password?, displayName?}`，邮箱与手机号至少一个；新用户角色为 `user`；201 `User` |
| GET | `/users/{id}` | `getUser` | `user:read` | 200 `User` |
| PATCH | `/users/{id}` | `updateUser` | `user:write` | `{displayName?, status?}`；禁用时吊销其全部会话；200 `User` |
| DELETE | `/users/{id}` | `deleteUser` | `user:write` | 202 `data: null`；与 `deleteMe` 相同的清理流程 |
| PUT | `/users/{id}/roles` | `setUserRoles` | `role:write` | `{roles: [code]}`，必须包含 `user`（缺少时 422，`field: roles`，`rule: contains`）；吊销该用户全部会话；200 `User` |
| GET | `/roles` | `listRoles` | `role:read` | 200 `[Role]` |
| GET | `/permissions` | `listPermissions` | `role:read` | 200 `[{code}]` |

- 禁用、删除（含 `deleteMe`）最后一个状态为 `active` 的 `admin`，或移除其 `admin` 角色：409 `role.lastAdmin`；检查与修改在同一事务内加锁
- 软删除立即释放该用户的邮箱、手机号与第三方身份，可被重新注册
- 引用不存在的角色代码：422，`rule` 为 `exists`

### 11.4 对象

| 对象 | 字段 |
|---|---|
| `User` | `id`、`email`、`emailVerified`、`phone`、`displayName`、`avatarUrl`、`status`（`active` / `disabled`）、`roles`（角色代码数组）、`hasPassword`、`createdAt`、`updatedAt` |
| `Session` | `id`、`userAgent`、`ip`、`createdAt`、`lastActiveAt`、`current` |
| `Role` | `code`、`name`、`permissions`（权限码数组） |
| `File` | `id`、`name`、`size`、`contentType`、`purpose`、`url`、`createdAt` |

`avatarUrl`、`File.url` 可以是带过期时间的签名地址，客户端不应长期缓存。

### 11.5 文件

| 方法 | 路径 | operationId | 鉴权 | 请求 | 成功 |
|---|---|---|---|---|---|
| POST | `/files` | `uploadFile` | JWT | `multipart/form-data`：`file`、`purpose` | 201 `File` |
| GET | `/files/{id}` | `getFile` | JWT | — | 200 `File`；只能读取本人上传的文件，否则 404 `common.notFound` |
| GET | `/files/{id}/content` | `getFileContent` | 签名查询参数 `expires`、`signature` | — | 200 文件内容，不包信封；签名无效或过期 404 `common.notFound`；只在 `File.url` 指向本服务时使用 |

- `purpose` 至少支持 `avatar`（图片，`image/jpeg`、`image/png`、`image/webp`，上限 5 MiB）；各服务可追加用途及其类型白名单、大小上限
- 超出上限 413 `file.tooLarge`，类型不符 422 `file.typeNotAllowed`；类型按文件内容识别，不信任客户端声明
- `getFileContent` 响应带 `X-Content-Type-Options: nosniff`；非图片类型带 `Content-Disposition: attachment`
- 直传对象存储等扩展接口由各服务自行提供

### 11.6 共享限流规则

| 规则名 | 适用接口 | 主体 | 速率 | 突发 |
|---|---|---|---|---|
| `auth-login` | `loginWithEmail`、`loginWithSms`、`loginWithProvider` | IP | 20 次 / 分钟 | 10 |
| `auth-login-account` | `loginWithEmail`、`loginWithSms` | 账号 | 10 次 / 小时 | 5 |
| `auth-register` | `registerWithEmail` | IP | 10 次 / 小时 | 3 |
| `auth-mail` | `resendVerificationEmail`（用户）、`forgotPassword`（IP） | 用户或 IP | 5 次 / 小时 | 3 |
| `auth-sms` | `sendSmsCode` | IP | 10 次 / 小时 | 5 |
| `auth-mail-account` | `forgotPassword` | 账号 | 3 次 / 小时 | 2 |
| `auth-password-verify` | `changePassword` 中校验 `currentPassword` | 用户 | 5 次 / 15 分钟 | 5 |
| `auth-refresh` | `refreshToken`，按 refresh 查到会话后计数，查不到时按 IP | 会话或 IP | 30 次 / 分钟 | 10 |

`auth-login-account`、`auth-password-verify` 只对失败计数，成功后清除该主体的键。
