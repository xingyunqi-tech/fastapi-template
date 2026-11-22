# 认证与权限

对外行为以 [platform-conventions.md](platform-conventions.md) §5、§11.1–§11.3 为准，本文只说明本仓库的实现位置与配置。

| 目录 | 内容 |
|---|---|
| `security/` | 密钥与 `kid`、JWT 签发与校验、吊销键、角色与权限、密码哈希、鉴权依赖 |
| `identity/auth` | 注册、登录、邮箱验证、找回与重置密码、刷新、登出 |
| `identity/sessions` | 会话与 refresh 令牌、宽限期重放、活跃时间、注销清理器 |
| `identity/me` | 当前用户资料、改密、会话管理、注销 |
| `identity/users` | 用户管理、角色分配、角色与权限列表 |
| `identity/account_deletion` | 注销流程（`deleteMe` 与 `deleteUser` 共用） |
| `identity/keys` | `GET /.well-known/jwks.json` |

## 接口

路径均在 `/api/v1/` 下，`jwks.json` 除外。

| 方法 | 路径 | operationId | 鉴权 | 权限 | 限流（次 / 窗口） |
|---|---|---|---|---|---|
| POST | `auth/email/register` | registerWithEmail | 公开 | | 3 / 1h |
| POST | `auth/email/login` | loginWithEmail | 公开 | | 10 / 60s |
| POST | `auth/email/verify` | verifyEmail | 公开 | | 默认 |
| POST | `auth/email/verify/resend` | resendVerificationEmail | JWT | | 3 / 1h |
| POST | `auth/password/forgot` | forgotPassword | 公开 | | 3 / 1h |
| POST | `auth/password/reset` | resetPassword | 公开 | | 默认 |
| POST | `auth/refresh` | refreshToken | 公开 | | 10 / 60s |
| POST | `auth/logout` | logout | JWT | | 默认 |
| GET | `me` | getMe | JWT | | 默认 |
| PATCH | `me` | updateMe | JWT | | 默认 |
| DELETE | `me` | deleteMe | JWT，10 分钟内登录 | | 默认 |
| PUT | `me/password` | changePassword | JWT | | 5 / 15min |
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

「默认」指 `THROTTLE_TTL` / `THROTTLE_LIMIT`（60s / 100 次），`THROTTLE_ENABLED=false` 时不限流。限流仍按路由计数，约定 §7 的 GCRA 共享规则尚未接入。

## 鉴权方式

每个路由必须在 `ApiRouter.endpoint(auth=...)` 中声明，未声明时应用启动失败。

| `Auth` | 依赖 | 行为 |
|---|---|---|
| `PUBLIC` | — | 不校验，携带的 `Authorization` 被忽略 |
| `JWT` | `CurrentPrincipal` | 校验 access 令牌与吊销键，失败 401；签发方另更新会话 `last_active_at` |
| `OPTIONAL_JWT` | `OptionalPrincipal` | 令牌缺失、无效、过期或已吊销时为 `None` |
| `MONITORING` | `monitoring_token` | `/api/v1/health`、`/metrics`：配置 `METRICS_TOKEN` 时校验 Bearer；未配置时非生产放行、生产拒绝 |

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
