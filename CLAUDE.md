> 本文件面向 AI 助手（`AGENTS.md` 与本文件内容相同）。人类开发者请看 [README.md](README.md)。

本仓库是 FastAPI 后端模板，HTTP API 契约与 nestjs-template（`release/exam-prep` 分支，提交号见 `openapi/SOURCE`）逐项一致，前端由 `/docs-json` 生成的客户端可在两个后端之间无改动切换。方案与决策见 [TEMPLATE_PLAN.md](TEMPLATE_PLAN.md)。

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

## 契约规则

`openapi/` 中的 nestjs 基线是契约的唯一来源。

| 文件 | 来源 | 修改方式 |
|---|---|---|
| `openapi/nestjs-baseline.json`、`validation.json`、`nest_handlers.json`、`SOURCE` | `make openapi-sync` | 禁止手改 |
| `db/nestjs-schema.sql`、`tests/contract/fixtures/validation-cases.json` | `make openapi-sync` | 禁止手改 |
| `src/app/contract/schemas.py` | `uv run --frozen python scripts/gen_schemas.py` | 禁止手改，`make lint` 校验一致 |
| `docs/contract.md` | `UPDATE_CONTRACT_DOC=1 make test-contract` | 只有「与 nestjs 的行为差异」一节手写 |

- 契约变更（路径、方法、operationId、tag、参数、请求 / 响应 DTO、校验规则、错误形状）必须先在 nestjs-template 落地，再在本仓库 `NESTJS_REF=<提交> make openapi-sync` → `gen_schemas.py` → 补实现与测试
- 不在本仓库新增基线之外的接口：契约测试要求每个已注册 operation 都存在于基线
- 请求 / 响应模型使用 `app.contract.schemas` 中与 nestjs DTO 同名的类；响应 schema 为未包装的 DTO
- 成功响应一律 HTTP 200（只有 `POST files/upload` 为 201）；错误统一为 `{code, message, data}` 信封，`message` 走 `common/i18n` 翻译
- 刻意保留的行为差异记录在 [docs/contract.md](docs/contract.md)「与 nestjs 的行为差异」，新增差异需写入该节

## 架构约定

- 路由一律用 `ApiRouter.endpoint(...)`，必须声明 `auth`（未声明时启动失败），operationId 与基线一致；RBAC 用 `permissions` / `any_permissions` 或 `ApiRouter(rbac=True)`
- 路由薄：业务在 service，数据访问在 repository
- 表名、列名、约束名与 nestjs 一致；ORM 列名 camelCase、属性 snake_case
- 每请求一个 `DbSession`，提交后副作用用 `after_commit`；路由外用 `context.database.transaction()`
- 配置只从 `app.config.get_settings()` 读取，不直接读 `os.environ`；新变量与 nestjs 同名
- Redis 键名与 nestjs 一致（`cache::`、`lock:`、`jwt:session:`、`session:rotated:`、`sms:code:` 等）
- 新模块：在 `factory.create_app` 中 `include_routes`（顺序与 nestjs AppModule 一致），在 `infrastructure/database/models.py` 导入模型，用 `alembic revision --autogenerate` 生成迁移并人工检查；已合并的迁移不修改

## 目录速查

| 目录 | 内容 |
|---|---|
| `src/app/factory.py` | `create_app()`、中间件、路由注册、lifespan |
| `src/app/common/` | 路由、信封、异常与翻译、校验引擎（复刻 class-validator）、中间件、分页、i18n |
| `src/app/config/` | Settings 分组 |
| `src/app/core/` | auth、users、session、permissions |
| `src/app/infrastructure/` | database、cache、redis 锁、throttler、logging、metrics、health、observability、queue、scheduler、audit、alerting、account_cleanup |
| `src/app/integrations/` | mail、sms、wechat、alipay |
| `src/app/modules/` | home、files、config、regions、notifications、addresses、favorites、feedbacks、consents、app_profile |
| `src/app/cli/` | `app` 命令、seed 数据、代码生成模板 |
| `migrations/` | Alembic |
| `tests/` | unit、integration、contract、parity、e2e |
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
make test                 # 单元 + 集成 + 契约（集成测试需 Docker）
make test-contract
make ci                   # check + test + security + workflow-lint
```

全部目标见 `make help` 与 [README.md](README.md)。

## 测试分层

| marker | 依赖 | 命令 |
|---|---|---|
| unit | 无 | `make test-unit` |
| integration | testcontainers | `make test` |
| contract | 无 | `make test-contract` |
| parity | 本服务与 nestjs 同时运行 | `make test-parity` |
| e2e | Docker Compose | `make test-e2e` |

marker 按目录自动添加。集成测试默认回滚模式；依赖真实提交的用例用 `committed_app` 并标 `@pytest.mark.committed`。改动接口行为时同时补集成测试与 parity 用例。详见 [docs/testing.md](docs/testing.md)。

## 文档

[architecture](docs/architecture.md) · [auth](docs/auth.md) · [database](docs/database.md) · [deploy](docs/deploy.md) · [testing](docs/testing.md) · [cli](docs/cli.md) · [contract](docs/contract.md)
