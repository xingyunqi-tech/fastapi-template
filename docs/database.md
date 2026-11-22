# 数据库

PostgreSQL 18、SQLAlchemy 2.1 async + asyncpg、Alembic。

## 连接配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | 必填 | libpq URI，如 `postgresql://user:pass@host:5432/db?sslmode=disable`；转换为 `postgresql+asyncpg` 驱动 URL |
| `DATABASE_SCHEMA` | `public` | 本服务独占的 schema，匹配 `[a-z_][a-z0-9_]{0,62}`，不能为 `pg_` 前缀或 `information_schema`；连接的 `search_path` 设为该值，迁移时不存在则创建 |
| `DATABASE_POOL_SIZE` | `10` | 每进程连接池大小，无溢出连接 |
| `DATABASE_POOL_TIMEOUT` | `10s` | 获取连接与新建连接的超时，须大于 0，格式同约定 §9 时长（`500ms`、`10s`、`1m30s`）；超时后该次请求返回 500 |

`DATABASE_URL` 支持的查询参数：

| 参数 | 说明 |
|---|---|
| `sslmode` | `disable` / `allow` / `prefer`（默认）/ `require` / `verify-ca` / `verify-full` |
| `sslrootcert` / `sslcert` / `sslkey` | 证书文件路径，须存在；只能与 `require` / `verify-ca` / `verify-full` 同用，`sslkey` 须与 `sslcert` 同用 |
| `application_name` | 写入连接的 `application_name` |

证书校验：

| `sslmode` | 行为 |
|---|---|
| `require` | 无 `sslrootcert` 时不校验证书；有则校验证书链 |
| `verify-ca` | 须给出 `sslrootcert`，校验证书链 |
| `verify-full` | 须给出 `sslrootcert`，校验证书链与主机名 |

其它参数、重复参数与多主机在启动时报错。连接会话时区固定为 UTC。

各进程的连接上限：

| 进程 | 上限 |
|---|---|
| API（每个 Granian worker） | `DATABASE_POOL_SIZE` |
| worker、scheduler（每个进程） | `DATABASE_POOL_SIZE` |
| `app seed`、`app users cleanup` | 1，一次性运行 |

部署时须满足各进程连接池之和 ≤ Postgres `max_connections`。

## 模型

- 列名与约束名 snake_case；约束与索引由 `Base.metadata` 的 `naming_convention` 命名：`pk_<表>`、`uq_<表>_<列>`、`ix_<表>_<列>`、`fk_<表>_<列>_<被引用表>`、`ck_<表>_<名>`
- 主键为 UUIDv7（`base.uuid_pk()`）：应用侧 `uuid.uuid7()` 生成，库侧默认 `uuidv7()`；`user_roles` 以 `(user_id, role)` 为复合主键，`account_deletions` 以 `user_id` 为主键
- 时间列为 `timestamptz`（`base.timestamp()`、`created_at()`、`updated_at()`）；API 输出为 `toISOString` 格式（毫秒、`Z`）
- 软删除列 `deleted_at`；注销用户时清空其唯一字段
- 每个模型声明 `__branch__`（`Branch.PLATFORM` / `IDENTITY` / `MODULES`），写入表的 `info["branch"]`，未声明时导入失败
- 全部模型在 `infrastructure/database/models.py` 汇总导入，Alembic 与测试从这里取 metadata

| 分支 | 表 |
|---|---|
| `platform` | `file`、`audit_logs` |
| `identity` | `user`、`role`、`status`、`session`、`permission`、`role_permission`、`account_cleanup` |
| `modules` | 生成器产出的业务模块 |

## 事务

| 用法 | 行为 |
|---|---|
| 路由参数 `session: DbSession` | 每请求一个 `AsyncSession`；正常返回提交，`AppError` 提交已写入的数据后抛出，其它异常回滚 |
| `after_commit(session, callback)` | 登记提交成功后执行的副作用（发邮件、投递任务）；失败只记日志 |
| `context.database.transaction()` | 路由外（CLI、worker、独立短事务）手动开启事务，语义同上 |
| `Database.bound_to(connection)` | 测试用：绑定到外层连接，提交只释放 SAVEPOINT |

## 迁移

```bash
make migrate                     # = uv run --frozen app migrate（alembic upgrade heads）
uv run --frozen alembic revision --autogenerate -m "add product" --head modules@head
uv run --frozen alembic check    # 全部分支已升级时，模型与迁移是否一致
uv run --frozen alembic upgrade platform@head
uv run --frozen alembic downgrade base
```

- 三个 Alembic 分支，目录 `migrations/versions/{platform,identity,modules}/`：`platform_0001` 为基础；`identity_0001`、`modules_0001` 声明 `depends_on` `platform_0001`（`users.avatar_file_id` 引用 `file`），`modules_0001` 不含操作
- `alembic.ini` 的 `version_locations` 按行列出三个目录（`path_separator = newline`），`recursive_version_locations = true`
- `migrations/env.py`：`DATABASE_SCHEMA` 不存在时先创建（已存在时迁移账号无需库级 CREATE 权限），版本表在该 schema 中；`--sql` 离线输出以 `CREATE SCHEMA IF NOT EXISTS` 与 `SET search_path` 开头；只比较 ORM 中的表，`-x branches=platform,modules` 进一步限定比较的分支（`autogenerate` 与 `check`）
- `alembic check` 要求全部分支已升级；只升级部分分支时用 `infrastructure/database/branches.py` 的 `schema_diff(connection, branches)` 比较
- 新迁移用 `--head <分支>@head`；自动生成的迁移需人工检查后再执行
- CI `migrations` job：`make migrate` → `alembic check` → `alembic downgrade base` → `make migrate`
- compose 中由一次性 `migrate` 服务执行 `app migrate`（`RUN_SEEDS=true` 时再执行 `app seed`），api 依赖其成功完成，多副本不会并发迁移

## Seed

`make seed`（`app seed`）只创建管理员，规则见 [auth.md](auth.md#种子数据)；角色与权限是代码常量，不入库。
