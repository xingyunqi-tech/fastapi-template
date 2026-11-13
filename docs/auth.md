# 认证与权限

实现位置：`core/auth`（路由、服务、鉴权依赖）、`core/session`、`core/permissions`、`integrations/{sms,wechat,alipay}`、`common/security.py`。

## 接口

全部成功响应为 HTTP 200。登录类接口返回 `LoginResponseDto`：`{token, refreshToken, tokenExpires(ms), user}`。

| 方法 | 路径（`/api/v1/` 下） | operationId | 鉴权 | 限流 |
|---|---|---|---|---|
| POST | `auth/email/login` | loginWithEmail | 公开 | 5 / 60s |
| POST | `auth/email/register` | registerWithEmail | 公开 | 默认 |
| POST | `auth/email/confirm` | confirmEmail | 公开 | 默认 |
| POST | `auth/email/confirm/new` | confirmNewEmail | 公开 | 默认 |
| POST | `auth/forgot/password` | forgotPassword | 公开 | 3 / 300s |
| POST | `auth/reset/password` | resetPassword | 公开 | 5 / 300s |
| GET | `auth/me` | getCurrentUser | JWT | 默认 |
| PUT | `auth/me` | updateCurrentUser | JWT | 10 / 60s |
| DELETE | `auth/me` | deleteCurrentUser | JWT | 默认 |
| POST | `auth/refresh` | refreshToken | refresh JWT | 默认 |
| POST | `auth/logout` | logout | JWT | 默认 |
| GET | `auth/sessions` | getMySessions | JWT | 默认 |
| DELETE | `auth/sessions/:id` | revokeMySession | JWT | 默认 |
| POST | `auth/sessions/revoke-others` | revokeOtherSessions | JWT | 默认 |
| PUT | `auth/password` | changePassword | JWT | 3 / 60s |
| POST | `auth/phone/send-code` | sendSmsCode | 公开 | 3 / 60s |
| POST | `auth/phone/login` | loginWithPhone | 公开 | 5 / 60s |
| POST | `auth/phone/sms-login` | loginWithSms | 公开 | 5 / 60s |
| POST | `auth/phone/register` | registerWithPhone | 公开 | 3 / 60s |
| POST | `auth/phone/reset-password` | resetPasswordByPhone | 公开 | 5 / 300s |
| POST | `auth/wechat/login` | loginWithWechat | 公开 | 10 / 60s |
| POST | `auth/wechat/phone-login` | loginWithWechatPhone | 公开 | 10 / 60s |
| POST | `auth/alipay/login` | loginWithAlipay | 公开 | 10 / 60s |

「默认」指 `THROTTLE_TTL` / `THROTTLE_LIMIT`（60s / 100 次），`THROTTLE_ENABLED=false` 时不限流。

## 鉴权方式

每个路由必须在 `ApiRouter.endpoint(auth=...)` 中声明，未声明时应用启动失败。

| `Auth` | 依赖 | 行为 |
|---|---|---|
| `PUBLIC` | — | 不校验 |
| `JWT` | `CurrentUser` | Bearer access 令牌，失败 401 |
| `OPTIONAL_JWT` | `OptionalUser` | 无令牌或校验失败时为 `None` |
| `REFRESH_JWT` | `CurrentRefreshUser` | 仅 `auth/refresh`，Bearer refresh 令牌 |
| `MONITORING` | `monitoring_token` | `/api/v1/health`、`/metrics`：配置 `METRICS_TOKEN` 时校验 Bearer；未配置时非生产放行、生产拒绝 |

## JWT

| 令牌 | 载荷 | 密钥 | 默认有效期 |
|---|---|---|---|
| access | `{id, role: {id}, sessionId, iat, exp}` | `AUTH_JWT_SECRET` | `AUTH_JWT_TOKEN_EXPIRES_IN=15m` |
| refresh | `{sessionId, hash, iat, exp}` | `AUTH_REFRESH_SECRET` | `AUTH_REFRESH_TOKEN_EXPIRES_IN=3650d` |
| 找回密码 | 含密码指纹 `sha256(hash)[:16]`，改密后失效 | `AUTH_FORGOT_SECRET` | `30m` |
| 邮箱确认 | — | `AUTH_CONFIRM_EMAIL_SECRET` | `1d` |

算法 HS256，时长按 `ms` 库语义解析。生产环境要求四个密钥均 ≥32 字符且互不相同。

### access 校验

1. 只读取 `Authorization: Bearer <token>`，载荷必须有 `id`、`sessionId`
2. 查缓存 `jwt:session:{sessionId}`（30s，`{statusId, roleId}`）；未命中时查库：会话存在、属于该用户、用户状态为 active / inactive，以数据库中的角色为准
3. 未命中缓存时更新会话 `lastActiveAt`（每会话最多 5 分钟一次，独立短事务）

### 刷新轮换

- 会话不存在或无关联用户 → 401
- `session.hash != payload.hash`：若等于 60 秒内被换下的旧 hash（`session:rotated:{id}`）视为并发重试，只返回 401；否则判为重放，删除会话并 401
- hash 一致时生成新 hash，以 `UPDATE … WHERE hash = old` 做 CAS，失败 401；成功返回新的 `{token, refreshToken, tokenExpires}`
- logout 软删当前会话；改密（`PUT auth/password`、`PUT auth/me` 带 `password`）软删其它会话；重置密码（邮件或短信）软删全部会话；定时任务每天 03:00 UTC 物理删除软删超过 7 天的会话

## 密码

客户端传 MD5 hex（32 位）。服务端哈希由 `PASSWORD_HASHER` 决定：

| 值 | 用途 |
|---|---|
| `argon2id`（默认，`m=19456,t=2,p=1`） | 独立部署 |
| `bcrypt`（cost 10） | 与 nestjs 共用数据库时 |

两种格式都能验证；登录成功且存储的哈希不是首选格式或参数时自动重哈希。每个进程同时进行的哈希数由 `PASSWORD_HASH_CONCURRENCY`（默认 2）限制。

## 登录方式

| 方式 | 行为 |
|---|---|
| 邮箱 | 注册后状态 inactive 并发送确认邮件；active / inactive 均可登录 |
| 手机 + 密码 | 失败 422 `{credentials: ...}` |
| 短信验证码 | 不自动注册，未知手机号 422 |
| 手机注册 | 直接 active 并返回令牌 |
| 微信 | 小程序 `jscode2session` / App OAuth，按 openId 自动建号 |
| 微信手机号 | 绑定 openId 与手机号，不合并已有账号；非大陆号拒绝 |
| 支付宝 | `alipay.system.oauth.token`，按 alipayOpenId 自动建号 |

### 短信验证码

| 项 | 规则 |
|---|---|
| 类型 | register 要求手机号不存在；login / reset_password / verify_identity 要求存在 |
| 存储 | `sms:code:{type}:{phone}`，TTL `SMS_CODE_EXPIRES`（300s），`SMS_CODE_LENGTH` 位数字 |
| 重发间隔 | `SMS_RESEND_INTERVAL`（60s），锁 `sms:send:{type}:{phone}` |
| 尝试次数 | `SMS_MAX_ATTEMPTS`（5），成功或超限后删除 |
| 发送 | 阿里云 Dysms；`SMS_MOCK_MODE=true` 只写日志（`.env.example` 默认开启） |

微信、支付宝配置：`WECHAT_MINI_APP_ID` / `WECHAT_MINI_APP_SECRET`（小程序）、`WECHAT_APP_ID` / `WECHAT_APP_SECRET`（App）、`ALIPAY_*`。

## RBAC

| 角色 | id | 状态 | id |
|---|---|---|---|
| admin | 1 | active | 1 |
| user | 2 | inactive | 2 |
| editor | 3 | | |
| reviewer | 4 | | |

- 权限枚举与默认映射：`core/permissions/service.py`（值与 nestjs 一致）；admin 拥有全部权限
- 角色权限读 `permission`、`role_permission` 表，表为空时回落代码默认值；缓存键 `permissions:v2:role-map`，5 分钟；`app permissions clear-cache` 清除
- 路由声明 `permissions=[...]`（全部满足）或 `any_permissions=[...]`（任一满足）
- 经过权限守卫（`ApiRouter(rbac=True)` 或声明了权限）却未声明任何权限的路由一律 403 `No permission declared for this route`
- 缺权限 403，`message` 为 `Missing permissions: a, b` / `Missing any of permissions: a, b`

## 会话

`session` 表记录 `userAgent`（≤512）、`ip`、`lastActiveAt`。`GET auth/sessions` 返回 `[{id, userAgent, ip, createdAt, lastActiveAt, current}]`；`DELETE auth/sessions/:id` 对非本人会话返回 404 `sessionNotFound`。会话变更时同步清除 `jwt:session:{id}` 缓存（立即一次，事务提交后再一次）。
