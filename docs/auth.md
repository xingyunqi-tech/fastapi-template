# 认证与权限

对外行为以 [platform-conventions.md](platform-conventions.md) §5、§11.1–§11.3 为准，本文只说明本仓库的实现位置与配置。

| 目录 | 内容 |
|---|---|
| `security/` | 密钥与 `kid`、JWT 签发与校验、吊销键、角色与权限、密码哈希、鉴权依赖 |
| `identity/auth` | 注册、登录、邮箱验证、找回与重置密码、刷新、登出 |
| `identity/sessions` | 会话与 refresh 令牌、宽限期重放、活跃时间、注销清理器 |
| `identity/me` | 当前用户资料、改密、会话管理、注销 |
| `identity/users` | 用户管理、角色分配、角色与权限列表 |
| `identity/providers` | 短信验证码与发送渠道、微信与支付宝客户端、第三方身份（`user_identities`） |
| `identity/account_deletion` | 注销流程（`deleteMe` 与 `deleteUser` 共用） |
| `identity/keys` | `GET /.well-known/jwks.json` |

## 接口

路径均在 `/api/v1/` 下，`jwks.json` 除外。

| 方法 | 路径 | operationId | 鉴权 | 权限 | 限流规则 |
|---|---|---|---|---|---|
| POST | `auth/email/register` | registerWithEmail | 公开 | | `auth-register` |
| POST | `auth/email/login` | loginWithEmail | 公开 | | `auth-login`、`auth-login-account` |
| POST | `auth/email/verify` | verifyEmail | 公开 | | 默认 |
| POST | `auth/email/verify/resend` | resendVerificationEmail | JWT | | `auth-mail`（按用户） |
| POST | `auth/password/forgot` | forgotPassword | 公开 | | `auth-mail`、`auth-mail-account` |
| POST | `auth/password/reset` | resetPassword | 公开 | | 默认 |
| POST | `auth/sms/send` | sendSmsCode | `login` 公开，`bind_phone` 需 JWT | | `auth-sms` |
| POST | `auth/sms/login` | loginWithSms | 公开 | | `auth-login`、`auth-login-account` |
| GET | `auth/providers` | listAuthProviders | 公开 | | 默认 |
| POST | `auth/providers/{provider}/login` | loginWithProvider | 公开 | | `auth-login` |
| POST | `auth/refresh` | refreshToken | 公开 | | `auth-refresh` |
| POST | `auth/logout` | logout | JWT | | 默认 |
| GET | `me` | getMe | JWT | | 默认 |
| PATCH | `me` | updateMe | JWT | | 默认 |
| DELETE | `me` | deleteMe | JWT，10 分钟内登录 | | 默认 |
| PUT | `me/password` | changePassword | JWT | | `auth-password-verify` |
| PUT | `me/phone` | bindPhone | JWT | | 默认 |
| PUT | `me/phone/wechat` | bindPhoneWithWechat | JWT | | 默认 |
| GET | `me/sessions` | listMySessions | JWT | | 默认 |
| DELETE | `me/sessions/{id}` | revokeMySession | JWT | | 默认 |
| POST | `me/sessions/revoke-others` | revokeOtherSessions | JWT | | 默认 |
| GET | `users` | listUsers | JWT | `user:read` | 默认 |
| POST | `users` | createUser | JWT | `user:write` | 默认 |
| GET | `users/{id}` | getUser | JWT | `user:read` | 默认 |
| PATCH | `users/{id}` | updateUser | JWT | `user:write` | 默认 |
| DELETE | `users/{id}` | deleteUser | JWT | `user:write` | 默认 |
| PUT | `users/{id}/roles` | setUserRoles | JWT | `role:write` | 默认 |
| GET | `roles` | listRoles | JWT | `role:read` | 默认 |
| GET | `permissions` | listPermissions | JWT | `role:read` | 默认 |
| GET | `/.well-known/jwks.json` | getJwks | 公开 | | 默认 |

共享规则的配额与主体见约定 §11.6，定义在 `infrastructure/throttler/rules.py`；`auth-login-account`、`auth-password-verify` 只计失败，成功后清除；`auth-refresh` 按会话计数，会话不存在时按 IP。「默认」指私有规则 `{OTEL_SERVICE_NAME}-default`（`RATE_LIMIT_DEFAULT`，默认 `10/s,burst=20`），JWT 路由按用户、其余按 IP。

## 鉴权方式

每个路由必须在 `ApiRouter.endpoint(auth=...)` 中声明，未声明时应用启动失败。

| `Auth` | 依赖 | 行为 |
|---|---|---|
| `PUBLIC` | — | 不校验，携带的 `Authorization` 被忽略 |
| `JWT` | `CurrentPrincipal` | 校验 access 令牌与吊销键，失败 401；签发方另更新会话 `last_active_at` |
| `OPTIONAL_JWT` | `OptionalPrincipal` | 令牌缺失、无效、过期或已吊销时为 `None` |
| `MONITORING` | `monitoring_token` | `/metrics`：配置 `METRICS_TOKEN` 时校验 Bearer，失败 401；未配置时非生产放行、生产拒绝 |

`RecentPrincipal` 额外要求 `auth_time` 在 10 分钟内，否则 403 `auth.reauthRequired`。

## 令牌

| 项 | 实现 |
|---|---|
| access | EdDSA（Ed25519），头部 `kid` 为 RFC 7638 指纹；声明 `iss`、`aud`、`sub`、`sid`、`roles`、`email_verified`、`auth_time`、`iat`、`exp`、`jti`；校验容忍 30 秒时钟偏差 |
| 签名密钥 | `AUTH_SIGNING_KEY`（PKCS#8 PEM，可用 `\n` 写成一行）；`app keys generate` 生成密钥对 |
| 轮换 | 新私钥写入 `AUTH_SIGNING_KEY`，旧公钥放入 `AUTH_VERIFY_KEYS` 并保留至少一个 access 有效期；JWKS 同时列出两者 |
| 吊销 | `auth:revoked:sid:{sid}`，TTL 为 access 有效期 + 60 秒；未配置 Redis 时用进程内字典；读写出错时 503 `common.serviceUnavailable`（`Retry-After: 5`），写入失败时请求事务回滚 |
| refresh | 32 字节随机值，库中存 SHA-256；轮换在会话行 `SELECT … FOR UPDATE` 下进行 |
| 宽限期 | 被换下的 refresh 30 秒内再次使用时返回同一对新令牌（AES-256-GCM 密文，密钥为 HKDF-SHA256(旧 refresh)）；超过 30 秒视为重放，吊销会话并 401 `auth.refreshTokenReused` |
| 会话期限 | 自登录起 `AUTH_REFRESH_TTL`（默认 720h），轮换不延长 |
| 一次性令牌 | 邮箱验证 24 小时、重置密码 30 分钟；每个用户每种用途只保留最新一条；重置令牌绑定签发时的密码哈希，改密后失效 |

吊销会话的操作：登出、`revokeMySession`、`revokeOtherSessions`、改密（保留当前会话）、重置密码、禁用用户、修改角色、注销账号、refresh 重放。

## 密码

- 长度 8–128 个 Unicode 码点，原文提交
- argon2id `m=19456, t=2, p=1`；参数不同的已有哈希在登录成功时重哈希（库中哈希未被并发修改时才写入）
- 账号不存在或未设置密码时仍校验一次固定假哈希
- 哈希在线程池中执行，每进程并发数 `PASSWORD_HASH_CONCURRENCY`（默认 2）

## 短信验证码

| 项 | 实现 |
|---|---|
| 规则 | 6 位数字，有效 5 分钟，与 `(phone, scene)` 绑定，单个验证码最多尝试 5 次；同一手机号冷却 60 秒、每个 UTC 日最多 10 条；1 小时内累计失败 10 次锁定 1 小时（失败按手机号计，未发过验证码的号码同样计入） |
| 存储 | Redis 键 `{OTEL_SERVICE_NAME}:sms:{code,attempts,cooldown,daily,failures,locked}:…:{手机号}`（手机号为 hash tag），只存验证码的 HMAC-SHA256（密钥由 `AUTH_SIGNING_KEY` 经 HKDF 派生）；比对与计次在 Lua 脚本内原子完成；未配置 Redis 时用进程内实现；Redis 出错时 503 |
| 渠道 | `SMS_PROVIDER=log`（默认）只写日志，生产环境不输出验证码；`aliyun` 以表单 body 调用 Dysmsapi `SendSms`，模板参数为 `{"code": "..."}`，需配齐 `SMS_ALIYUN_*` |
| 失败 | 渠道调用失败 502 `common.badGateway`，并撤销本次冷却与当日计数 |
| 登录 | `loginWithSms` 对未注册手机号自动注册（无邮箱、无密码）；`bindPhone` 只接受 `scene=bind_phone` 的验证码 |

## 第三方登录

| provider | 配置 | 换取 |
|---|---|---|
| `wechat_miniapp` | `WECHAT_MINIAPP_APP_ID` / `WECHAT_MINIAPP_SECRET` | `jscode2session` 的 `openid`、`unionid` |
| `wechat_app` | `WECHAT_APP_APP_ID` / `WECHAT_APP_SECRET` | `sns/oauth2/access_token` 的 `openid`、`unionid` |
| `alipay` | `ALIPAY_APP_ID` / `ALIPAY_PRIVATE_KEY` / `ALIPAY_PUBLIC_KEY` | `alipay.system.oauth.token` 的 `open_id`（旧应用为 `user_id`）；请求用应用私钥签名，响应用支付宝公钥验签 |

- 凭据配齐即启用；路由始终注册，未启用时 404 `auth.providerDisabled`
- 上游返回 `code` 无效（微信 errcode 40029 / 40163 / 42003，支付宝 `isv.code-*`）时 422 `auth.providerCodeInvalid`；网络错误、超时、非 2xx、其他错误码、缺少 openid 或验签失败时 502 `common.badGateway`
- 身份存 `user_identities`，`(provider, openid)` 唯一；微信两个 provider 之间按 `unionid` 关联到同一账号，命中后回填缺失的 `unionid`；首次登录（含经 `unionid` 关联）在 savepoint 中写入，并发冲突时回读已写入的身份；不同 provider 携带同一 `unionid` 同时首次登录时可能建出两个账号，之后按最早的身份关联
- `bindPhoneWithWechat` 用小程序 `stable_token`（缓存至过期前 5 分钟，失效时强制刷新一次）调用 `getuserphonenumber`
- 注销时删除该用户的 `user_identities`，同一第三方身份可重新注册

## 角色与权限

| 角色 | 权限 |
|---|---|
| `admin` | 全部（平台权限与本服务登记的私有权限） |
| `user` | 本服务登记时授予 `user` 的私有权限 |

- 角色与权限是代码常量（`security/rbac.py`），不入库；用户角色存 `user_roles`
- 私有权限用 `rbac.register(code, roles=...)` 登记，`admin` 自动拥有
- 路由声明 `permissions=[...]`（全部满足）或 `any_permissions=[...]`（任一满足）；经过权限守卫却未声明权限、或缺少权限时 403 `common.forbidden`
- 禁用、降级、删除最后一个 active `admin` 时 409 `role.lastAdmin`（锁定现有 admin 的 `user_roles` 行后再计数）

## 种子数据

`app seed` 在没有 active admin 时创建管理员（角色 `admin` + `user`，邮箱已验证）：非生产为 `admin@example.com` / `Secret00`；生产取 `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`，未设置时不创建。
