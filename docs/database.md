# 数据库

PostgreSQL 18（与 nestjs 共库或 parity 对照时为 17.11）、SQLAlchemy 2.1 async + asyncpg、Alembic。

## 连接配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | 空 | 设置时优先，驱动统一替换为 `postgresql+asyncpg` |
| `DATABASE_HOST` / `PORT` / `USERNAME` / `PASSWORD` / `NAME` | `localhost` / `5432` / `root` / `secret` / `api` | 未设置 `DATABASE_URL` 时使用 |
| `DATABASE_MAX_CONNECTIONS` | `100` | 连接池：`pool_size = min(N, 20)`，其余为 overflow |
| `DATABASE_SSL_ENABLED` / `DATABASE_REJECT_UNAUTHORIZED` / `DATABASE_CA` / `DATABASE_KEY` / `DATABASE_CERT` | `false` / `true` / 空 | 语义同 node-postgres ssl 选项，CA / KEY / CERT 为 PEM 文本 |
| `DB_SCHEMA_OWNER` | `alembic` | `external` 表示表结构由 nestjs 迁移管理，见下文 |

连接会话时区固定为 UTC。

## 模型

- 表名、列名、约束名、索引与 nestjs 一致（`db/nestjs-schema.sql` 为对照快照）；列名 camelCase，ORM 属性 snake_case：`first_name: Mapped[str] = mapped_column("firstName", ...)`
- 时间列为 `timestamp without time zone`，约定存 UTC（`base.timestamp()`、`created_at()`、`updated_at()`）；API 输出为 `toISOString` 格式（毫秒、`Z`）
- 软删除列 `deletedAt`；已删除用户的唯一字段由部分索引释放
- uuid 主键（`file`、`user_consent`）默认值 `uuid_generate_v4()`，初始迁移创建 `uuid-ossp` 扩展
- 新约束遵循 `Base.metadata` 的 `naming_convention`（`IDX_` / `UQ_` / `CK_` / `FK_` / `PK_` 前缀）
- 全部模型在 `infrastructure/database/models.py` 汇总导入，Alembic 与测试从这里取 metadata

表：`user`、`role`、`status`、`session`、`permission`、`role_permission`、`file`、`audit_logs`、`config`、`region`、`notification`、`notification_setting`、`address`、`favorite`、`feedback`、`user_consent`、`account_cleanup`、`admin_alert`。

## 事务

| 用法 | 行为 |
|---|---|
| 路由参数 `session: DbSession` | 每请求一个 `AsyncSession`；正常返回提交，`HttpError` 提交已写入的数据后抛出，其它异常回滚 |
| `after_commit(session, callback)` | 登记提交成功后执行的副作用（发邮件、投递任务）；失败只记日志 |
| `context.database.transaction()` | 路由外（CLI、worker、独立短事务）手动开启事务，语义同上 |
| `Database.bound_to(connection)` | 测试用：绑定到外层连接，提交只释放 SAVEPOINT |

## 迁移

```bash
make migrate                                          # = uv run --frozen app migrate（alembic upgrade head）
uv run --frozen alembic revision --autogenerate -m "add product"
uv run --frozen alembic check                         # 模型与迁移是否一致
uv run --frozen alembic downgrade base
```

- 迁移文件在 `migrations/versions/`，命名 `<rev>_<slug>.py`；当前只有 `0001_initial_schema.py`
- `migrations/env.py` 使用 async 引擎，开启 `compare_type`、`compare_server_default`，连接参数取自 `DatabaseSettings`
- 自动生成的迁移需人工检查后再执行；已合并的迁移不修改，变更通过新迁移完成
- CI `migrations` job：`make migrate` → `alembic check` → `alembic downgrade base` → `make migrate`
- compose 中由一次性 `migrate` 服务执行 `app migrate && app permissions clear-cache`（`RUN_SEEDS=true` 时再执行 `app seed`），api 依赖其成功完成，多副本不会并发迁移

## 与 nestjs 共用数据库

| 部署方式 | 表结构归属 | 设置 |
|---|---|---|
| 独立部署（默认） | Alembic | `DB_SCHEMA_OWNER=alembic`、`PASSWORD_HASHER=argon2id` |
| 与 nestjs 共库 | nestjs（TypeORM 迁移） | `DB_SCHEMA_OWNER=external`、`PASSWORD_HASHER=bcrypt` |

`external` 时 `app migrate` 直接跳过；API 启动时检查当前 schema 是否包含全部 ORM 模型的表和列，缺失时启动失败并列出缺失项。

## Seed

`make seed`（`app seed`）在一个事务内写入，已存在的数据不覆盖：

| 数据 | 来源 |
|---|---|
| 角色、状态、权限、角色权限、配置项 | `src/app/cli/seed_data/seed.json` |
| 行政区划 | `src/app/cli/seed_data/region-data.json`（`region` 表为空时写入） |
| 管理员 | 无 admin 用户时创建：非生产为 `admin@example.com` / `Secret00`；生产取 `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`，未设置则不创建 |
| 示例用户 | 非生产且无 user 角色用户时创建 `john.doe@example.com` / `Secret00` |

完成后清除角色权限缓存。账号与 nestjs seed 一致，前端模板可直接登录。
