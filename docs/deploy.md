# 部署

## 镜像

`Dockerfile` 多阶段，基础镜像 `python:3.15.2-slim`，uv 取自 `ghcr.io/astral-sh/uv:0.13.7`（均按 digest 固定）。

| 目标 | 内容 |
|---|---|
| `builder` | `uv sync --frozen --no-dev`：先装依赖层再装项目，BuildKit cache mount，`UV_COMPILE_BYTECODE=1` |
| `development` | 在 builder 上安装 dev 依赖并复制整个仓库，`CMD fastapi dev`（`make up`、test 组合使用） |
| `production` | 只含 `.venv`、`src`、`alembic.ini`、`migrations`、`openapi`；非 root 用户（uid 10001），卸载 pip / setuptools；`HEALTHCHECK` 请求 `/api/v1/health/live`；`CMD python -m app` |

```bash
make docker-build                  # docker build --target production -t fastapi-temp:local .
make docker-build IMAGE=myapp:dev
```

`.dockerignore` 为白名单写法。同一镜像通过不同命令运行四种进程：

| 进程 | 命令 |
|---|---|
| api | 默认 `CMD`（Granian，`WEB_CONCURRENCY` 个进程） |
| worker | `app worker` |
| scheduler | `app scheduler` |
| migrate | `app migrate && app permissions clear-cache`，`RUN_SEEDS=true` 时追加 `app seed` |

## Compose

文件在 `docker/`，从仓库根以 `-f` 组合：

| 场景 | 组合 | 入口 |
|---|---|---|
| 本地依赖 | `infra.compose.yaml` | `make infra` |
| 开发服务栈 | base + redis + postgres + dev | `make up` / `make down` |
| 容器内测试 | base + redis + postgres + test | 见下 |
| e2e / CI | base + postgres + redis + ci | `make test-e2e` |
| 服务器 | `docker-compose.ecs.yaml` | `deploy/manage.sh` |

```bash
docker compose -f docker/docker-compose.base.yaml -f docker/docker-compose.redis.yaml \
  -f docker/docker-compose.postgres.yaml -f docker/docker-compose.test.yaml up --build
```

| 文件 | 服务 / 作用 |
|---|---|
| `infra.compose.yaml` | postgres:18.6（:5432）、redis:8.10.3（:6379）、mailpit（:1025 SMTP、:8025 UI），项目名 `fastapi-temp-infra` |
| `docker-compose.base.yaml` | `pidns`（api 与 worker 共享 PID 命名空间）、`mailpit`（UI 宿主端口 `MAIL_CLIENT_PORT`，默认 1080）、`migrate`、`api`（:3000，`env_file: .env.example`） |
| `docker-compose.postgres.yaml` | `postgres`；`adminer`（profile `tools`，:8080） |
| `docker-compose.redis.yaml` | `redis`、`worker`、`scheduler`，为 api / migrate 设置 `REDIS_ENABLED=true`；`redisinsight`（profile `tools`，:5540） |
| `docker-compose.dev.yaml` | development 镜像；`develop.watch` 同步 `src/` 并重启，`pyproject.toml` / `uv.lock` 变更时重建；migrate 设 `RUN_SEEDS=true` |
| `docker-compose.test.yaml` | development 镜像、`APP_ENV=test`，挂载 `src/`、`tests/` |
| `docker-compose.ci.yaml` | 生产镜像、`APP_ENV=production`、CI 用密钥与 `METRICS_TOKEN`、`TRUST_PROXY=1`、`WEB_CONCURRENCY=2`，端口随机分配 |
| `docker-compose.ecs.yaml` | 服务器：api、worker、scheduler、migrate、postgres、redis；镜像 `IMAGE_FULL_NAME`，配置取部署目录 `.env` |

api 与 worker 挂载同一 `prometheus-multiproc` 卷并设 `PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus`，worker 的 `queue_jobs_total` 由 api 的 `/metrics` 输出。

## 服务器部署

### 首次准备

1. 服务器安装 Docker 与 Compose 插件
2. 创建部署目录（对应 secret `<PREFIX>_PATH`），放置 `.env`（参考 `.env.example`）
3. 在仓库配置 Secrets / Variables（见 deploy.yml）后手动触发 Deploy workflow

生产 `.env` 检查项：

| 变量 | 要求 |
|---|---|
| `AUTH_JWT_SECRET`、`AUTH_REFRESH_SECRET`、`AUTH_FORGOT_SECRET`、`AUTH_CONFIRM_EMAIL_SECRET` | ≥32 字符且互不相同，否则启动失败 |
| `MAIL_HOST`、`MAIL_DEFAULT_EMAIL`、`MAIL_DEFAULT_NAME` | 必填，否则启动失败 |
| `METRICS_TOKEN` | ≥16 字符；未设置时生产拒绝访问 `/metrics` 与 `/api/v1/health` |
| `FRONTEND_DOMAIN` | CORS 白名单，逗号分隔 |
| `DATABASE_*`、`PASSWORD_HASHER`、`DB_SCHEMA_OWNER` | 见 [database.md](database.md) |
| `FILE_DRIVER` 及 S3 变量 | `s3` / `s3-presigned` 缺凭据、桶或区域时启动失败 |
| `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` | `RUN_SEEDS=true` 时创建首个管理员 |
| `TRUST_PROXY` | 反向代理层数 |

`APP_ENV=production`、`REDIS_ENABLED=true`、`PROMETHEUS_MULTIPROC_DIR` 等由 `docker-compose.ecs.yaml` 设置。

### deploy/manage.sh

服务器上 `manage.sh` 位于 `<部署目录>/deploy/`，compose 文件位于 `<部署目录>/docker/`。

| 命令 | 作用 |
|---|---|
| `start` | 先 `compose run --rm migrate`，再 `up -d --remove-orphans`，轮询容器内 `/api/v1/health/ready`（30 次 × 5s） |
| `stop` | `compose stop` |
| `restart` | `compose restart` |
| `remove` / `rm` | `compose down -v --remove-orphans`（删除数据卷） |
| `status` / `ps` | `compose ps` |
| `logs` | `compose logs -f` |
| `health` | 宿主访问 `http://localhost:${APP_PORT:-3000}/api/v1/health/ready` |
| `cleanup <镜像>` | 保留最新 3 个镜像 |
| `help` | 帮助 |

## GitHub Actions

| workflow | 触发 | 内容 |
|---|---|---|
| `check.yml` | push / PR 到 main、master、`release/**`，手动 | 见下表 |
| `deploy.yml` | 手动（输入 `deploy`、`target`: aliyun / tencent / all） | 构建 amd64 + arm64 生产镜像（SBOM、provenance）推 GHCR，`deploy=true` 时同时推腾讯云 TCR；按目标矩阵 scp `docker/docker-compose.ecs.yaml` 与 `deploy/manage.sh`，SSH 拉镜像（3 次重试）→ migrate → `up -d --remove-orphans` → 轮询 `/api/v1/health/ready` 30 次 → 保留最新 3 个镜像；成功后调用 cleanup-registry；notify 输出摘要 |
| `cleanup-registry.yml` | 每周一 03:00（北京时间）、手动（`dry_run`）、deploy 调用 | GHCR 始终保留 `latest`（`exclude-tags`），另保留最近 2 个带 tag 的版本并删除无 tag 版本；TCR 保留 `latest` 指向的镜像及最近推送的共 2 个，未配置密钥时跳过 |
| `nestjs-drift.yml` | 每天 01:00 UTC、手动 | 对 nestjs-template `release/exam-prep` HEAD 运行 `scripts/openapi-sync.sh`，与入库的 `openapi/`、`db/nestjs-schema.sql`、`tests/contract/fixtures/validation-cases.json` 比对，有差异时失败并在摘要输出 diff；不修改、不提交 |

check.yml 的 job：

| job | 内容 | 依赖 |
|---|---|---|
| lint | `make lint`、hadolint | – |
| workflow-lint | actionlint、zizmor（min-severity medium，忽略项见 `.github/zizmor.yml`） | – |
| type-check | mypy；ty（`continue-on-error`） | – |
| test | `make test`（testcontainers），上传 coverage.xml | – |
| migrations | postgres service：upgrade → `alembic check` → downgrade base → upgrade | – |
| contract | `make test-contract` | – |
| security | `make security`（pip-audit）、gitleaks（只扫本次 push / PR 引入的提交） | – |
| build | `uv build`、生产镜像构建 | – |
| image-scan | 生产镜像 + Trivy（HIGH / CRITICAL，有修复版本） | – |
| e2e | `make test-e2e`，失败上传容器日志 | lint、type-check、test、build、migrations、workflow-lint、security |
| parity | 构建 `openapi/SOURCE` 中提交的 nestjs 镜像并启动；本服务 `make infra`、`migrate`、`seed`、`serve` + worker；`make test-parity`；nestjs `test/user/auth.e2e-spec.ts` 指向本服务；表结构与 `db/nestjs-schema.sql` 比对 | build、migrations |

### Secrets 与 Variables

| 名称 | 类型 | 用途 |
|---|---|---|
| `ALIYUN_HOST` / `ALIYUN_PORT` / `ALIYUN_USER` / `ALIYUN_SSH_KEY` / `ALIYUN_PATH` | Secret | 阿里云 ECS（PORT 默认 22） |
| `TENCENT_HOST` / `TENCENT_PORT` / `TENCENT_USER` / `TENCENT_SSH_KEY` / `TENCENT_PATH` | Secret | 腾讯云 CVM |
| `TENCENT_TCR_PASSWORD` | Secret | TCR 登录 |
| `TENCENT_TCR_REGISTRY` / `TENCENT_TCR_NAMESPACE` / `TENCENT_TCR_USERNAME` | Variable | TCR 地址、命名空间（仓库 `<命名空间>/fastapi-temp`）、用户名 |
| `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY` | Secret | cleanup-registry 清理 TCR |
| `TENCENT_TCR_REGION` | Variable | TCR 区域，默认 ap-guangzhou |

Environments：`aliyun-production`、`tencent-production`。所有 Action 按 commit SHA 固定版本。

## 依赖更新

Renovate（`.github/renovate.json`）：每周一 9 点前（Asia/Shanghai）；覆盖 uv（pep621）、Dockerfile、`docker/` 下 compose、GitHub Actions 及 workflow 中的 `*_IMAGE` 镜像；minor / patch / digest 分组自动合并，major 单独 PR 不自动合并；Postgres / Redis 只接收同大版本更新；每周 lock 文件维护。
