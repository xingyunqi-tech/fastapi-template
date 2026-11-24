> 本文件面向 AI 助手（`AGENTS.md` 与本文件内容相同）。人类开发者请看 [README.md](README.md)。

本仓库是独立的 Python / FastAPI 后端模板，可单独部署，也可作为 monorepo 中的签发方或验证方服务。实施方案与批次见 [TEMPLATE_PLAN.md](TEMPLATE_PLAN.md)；改造按 §15 分批进行，尚未完成的批次中 v1 实现仍在原位。

## 协作规则

### 改动范围

- 只做用户点名的事。顺手发现的问题写进报告，不改代码
- 汇报只讲行为改动与验证结果，用户没问就不附「还可以改的清单」

### 提交与推送

- 只有用户当次明确说「提交 / commit」才能提交；「开始做吧」「继续」「按这个改」「可以」都只是让你写代码
- 一次提交授权只覆盖那一批改动，不延续到后续任务
- 改完停在工作区，汇报改了什么和验证结果，等用户 review；想提交就先问
- push 更严：没听到「推 / push」不推
- 提交信息：`type(scope): subject`，type 取 feat / fix / docs / style / refactor / perf / test / build / ci / chore / revert，标题 ≤72 字符、结尾不加句点（commit-msg 钩子 `scripts/check_commit_msg.py` 校验）；subject 用中文

### 注释与命名

- 禁止段落式注释：不复述代码在做什么，不写「为什么不用另一种写法」的论证，不在 docstring 里写背景故事
- 注释只描述当前代码，不写变更历史：禁止「原来 / 之前 / 现在改 / 不再 / 已修复 / 回归 / 新增了 / 移除了」等字眼，变更原因写进 commit message
- 测试用例名只陈述行为，不写「回归 / 防止 / 修复」
- 文档同样只描述当前状态

### 派发子任务

通过 Agent 派发的子任务同样受以上规则约束：派发时把注释规则与提交推送规则原文写进提示词，并要求子任务收尾时统计新增注释行数。

## 约定优先

- 对外行为（路径、信封、错误码、校验、分页、数据类型、令牌、请求头、限流、日志、公共环境变量、身份与文件接口）以 [docs/platform-conventions.md](docs/platform-conventions.md) 为准；该文件与 go-template、springboot-template 逐字节相同，本仓库不单独修改
- 新增 errorKey、共享限流规则或共享 Redis 键，须先在约定中登记并三仓库同步，再实现
- 方案与约定冲突时以约定为准，并在 TEMPLATE_PLAN.md 中修正

## 架构约定

- 路由一律用 `ApiRouter.endpoint(...)`，必须声明 `auth`（未声明时启动失败）；RBAC 用 `permissions` / `any_permissions` 或 `ApiRouter(rbac=True)`
- 路由薄：业务在 service，数据访问在 repository
- 每请求一个 `DbSession`，提交后副作用用 `after_commit`；路由外用 `context.db.transaction()`
- 配置只从 `app.config.get_settings()` 读取，不直接读 `os.environ`
- 新模块：在 `factory.mount_routes` 中 `include_routes`，在 `infrastructure/database/models.py` 导入模型，用 `alembic revision --autogenerate` 生成迁移并人工检查；已合并的迁移不修改

## 目录速查

| 目录 | 内容 |
|---|---|
| `src/app/factory.py` | `create_app()`、中间件、路由注册、lifespan |
| `src/app/common/` | 路由、信封、异常与翻译、校验、中间件、分页、i18n |
| `src/app/config/` | Settings 分组 |
| `src/app/security/` | 密钥、JWT、吊销、角色与权限、密码、鉴权依赖 |
| `src/app/identity/` | auth、sessions、me、users、providers（sms、wechat、alipay）、account_deletion、keys |
| `src/app/infrastructure/` | database、cache、redis（键前缀、锁）、throttler、logging、telemetry、health、queue、scheduler、audit、events、mail、account_data |
| `src/app/modules/` | files、notes |
| `src/app/cli/` | `app` 命令、seed 数据、代码生成模板 |
| `migrations/` | Alembic |
| `tests/` | unit、integration、e2e |
| `docker/`、`deploy/`、`.github/workflows/` | compose、manage.sh、CI / CD |

## 常用命令

```bash
make install              # 依赖 + prek 钩子
make infra                # 本地 Postgres / Redis / Mailpit
make migrate && make seed
make dev                  # http://localhost:3000
uv run --frozen app worker
make fmt                  # 格式化 + 自动修复
make check                # lint + typecheck + 单元测试（pre-push 钩子执行）
make test                 # 单元 + 集成（集成测试需 Docker）
make ci                   # check + test + security + workflow-lint
```

全部目标见 `make help` 与 [README.md](README.md)。

## 测试分层

| marker | 依赖 | 命令 |
|---|---|---|
| unit | 无 | `make test-unit` |
| integration | testcontainers | `make test` |
| e2e | Docker Compose | `make test-e2e` |

marker 按目录自动添加。集成测试默认回滚模式；依赖真实提交的用例用 `committed_app` 并标 `@pytest.mark.committed`。改动接口行为时同时补集成测试。详见 [docs/testing.md](docs/testing.md)。

## 文档

[platform-conventions](docs/platform-conventions.md) · [architecture](docs/architecture.md) · [auth](docs/auth.md) · [database](docs/database.md) · [deploy](docs/deploy.md) · [testing](docs/testing.md) · [cli](docs/cli.md)
