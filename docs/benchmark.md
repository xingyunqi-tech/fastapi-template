# 压测（k6）

登录、刷新、`auth/me`、用户列表四个接口在同一台机器、同样资源限制下对比 fastapi-temp 与 nestjs-template（`02fdb4d`）。测试日期 2026-11-13。

## 环境

| 项 | 值 |
|---|---|
| 机器 | MacBook Pro（MacBookPro18,2），Apple M1 Max，10 核，32 GB |
| Docker | Docker Desktop，Engine 28.5.1，VM 分配 10 CPU、7.75 GiB 内存 |
| 压测工具 | `grafana/k6:1.3.0`，容器接入被测栈的 compose 网络，直连 `http://api:3000` |
| api 容器限制 | 两边都是 `cpus: 2`、`mem_limit: 1g`（swap 上限随 Docker 默认，即另有 1 GiB swap）；Postgres、Redis 等不限 |

| | fastapi-temp | nestjs-template |
|---|---|---|
| 镜像 | `fastapi-temp:bench`（本仓库 `Dockerfile` 的 `production` target） | `fastapi-temp/nestjs-ref:02fdb4def2df`（nestjs `Dockerfile` 的 `production` target） |
| 运行时 | Python 3.15.2，Granian 2.8.7（ASGI），FastAPI，SQLAlchemy + asyncpg | Node 24.22.2，NestJS 12.4.2（Express），TypeORM 1.1.2 + pg |
| 并发模型 | `WEB_CONCURRENCY=2`：Granian 2 个 worker 进程，每个进程一个事件循环；密码哈希经 `anyio.to_thread` 放到线程池，每进程同时最多 2 个（`PASSWORD_HASH_CONCURRENCY=2`）；镜像设 `MALLOC_ARENA_MAX=2`、`MALLOC_MMAP_THRESHOLD_=131072` | 单个 Node 进程（未启用 cluster），一个事件循环；bcryptjs 为纯 JS 实现，在主线程上分段执行 |
| 密码哈希 | argon2id（`PASSWORD_HASHER=argon2id`）：`t=2`、`m=19456`（19 MiB）、`p=1`；种子用户哈希为 `$argon2id$v=19$m=19456,t=2,p=1` | bcryptjs 3.0.3，`genSalt()` 默认 10 轮；种子用户哈希为 `$2b$10$…` |
| 数据库连接池 | `DATABASE_MAX_CONNECTIONS=100` 按 worker 数均分：每个 worker `pool_size=20`、`max_overflow=30`，合计最多 100 | `DATABASE_MAX_CONNECTIONS=100`，单进程最多 100 |
| Postgres / Redis | `postgres:18.6-alpine`、`redis:8.10.3-alpine`（项目 `bench-fastapi`） | `postgres:17.11-alpine`、`redis:7.4.12-alpine`（项目 `bench-nestjs`） |
| 模式 | `APP_ENV=production`（ci overlay） | `NODE_ENV=production` |
| 日志 | info 级 JSON，每个请求一条访问日志 | info 级 JSON（pino），每个请求一条访问日志 |
| 限流 | `THROTTLE_ENABLED=false` | `THROTTLE_ENABLED=false` |
| 其他进程 | 同栈另有 worker、scheduler、mailpit（不限资源） | 同栈另有 mailpit |

fastapi 栈由 `docker/docker-compose.{base,postgres,redis,ci}.yaml` 加一个 override 组成，override 只替换镜像并给 api 加资源限制；migrate 跑迁移和 seed。nestjs 栈由 `scripts/nestjs/nestjs.compose.yaml` 加 override 起，env 来自 nestjs `02fdb4d` 的 `env-example-relational`，替换项为 `THROTTLE_ENABLED=false`、各 `*_SECRET` 补足 32 位（生产模式启动校验要求），override 设 `NODE_ENV=production`、`LOG_LEVEL=info`、`LOG_PRETTY_PRINT=false`。

## 方法

- 接口与账号：
  - `POST /api/v1/auth/email/login`：`john.doe@example.com`，密码字段为 `md5("Secret00")`。
  - `POST /api/v1/auth/refresh`：setup 阶段为每个 VU 登录一次拿到独立会话，每次请求带上一次返回的新 refreshToken。
  - `GET /api/v1/auth/me`：`john.doe@example.com` 的 access token。
  - `GET /api/v1/users?limit=10`：`admin@example.com` 的 access token。
- 每个接口单独跑一次 k6：`constant-vus`，总时长 40 s，前 10 s 为预热，只统计之后 30 s 内发出的请求（按 `phase` 标签过滤）；VU 无思考时间。
- VU 分 10、50、100 三档。按档位、接口顺序执行，每组内两个后端交替先后（第一组 fastapi 先，第二组 nestjs 先，依此类推）。
- 指标：RPS = 统计窗口内完成请求数 / 30 s；p50、p95、p99 为 `http_req_duration`；错误率为 `http_req_failed`（非 2xx/3xx 或网络错误）。

## 结果

| 接口 | VU | 后端 | 请求数 | RPS | p50 (ms) | p95 (ms) | p99 (ms) | 错误率 |
|---|---|---|---|---|---|---|---|---|
| login | 10 | fastapi | 1260 | 42 | 225.3 | 384.8 | 399.5 | 0% |
| login | 10 | nestjs | 400 | 13.3 | 740.6 | 968 | 1073.2 | 0% |
| login | 50 | fastapi | 1173 | 39.1 | 1275.8 | 1565.2 | 1685.4 | 0% |
| login | 50 | nestjs | 405 | 13.5 | 3658.8 | 4208.6 | 5259.9 | 0% |
| login | 100 | fastapi | 1026 | 34.2 | 2831.1 | 3583.1 | 3697 | 0% |
| login | 100 | nestjs | 379 | 12.6 | 4768.3 | 12315.9 | 14712.4 | 0% |
| refresh | 10 | fastapi | 18873 | 629.1 | 17 | 26.5 | 35.8 | 0% |
| refresh | 10 | nestjs | 21986 | 732.9 | 13.4 | 19.2 | 23.6 | 0% |
| refresh | 50 | fastapi | 18790 | 626.3 | 82.3 | 114.9 | 220.6 | 0% |
| refresh | 50 | nestjs | 26711 | 890.4 | 61.4 | 77.5 | 110.4 | 0% |
| refresh | 100 | fastapi | 16174 | 539.1 | 199.7 | 337.2 | 481.5 | 0% |
| refresh | 100 | nestjs | 27652 | 921.7 | 125.4 | 196.2 | 281.4 | 0% |
| me | 10 | fastapi | 27718 | 923.9 | 10.1 | 16.4 | 20 | 0% |
| me | 10 | nestjs | 39653 | 1321.8 | 7.2 | 10.2 | 14 | 0% |
| me | 50 | fastapi | 26633 | 887.8 | 57.8 | 89.6 | 140 | 0% |
| me | 50 | nestjs | 40708 | 1356.9 | 34 | 54.9 | 85.3 | 0% |
| me | 100 | fastapi | 25511 | 850.4 | 114.5 | 173.4 | 240.4 | 0% |
| me | 100 | nestjs | 37393 | 1246.4 | 70.6 | 140.2 | 238 | 0% |
| users | 10 | fastapi | 25423 | 847.4 | 12.4 | 19.7 | 23.4 | 0% |
| users | 10 | nestjs | 29395 | 979.8 | 8.3 | 14.2 | 39.4 | 0% |
| users | 50 | fastapi | 26040 | 868 | 55.9 | 80.9 | 117.3 | 0% |
| users | 50 | nestjs | 34559 | 1152 | 38 | 66.5 | 152.4 | 0% |
| users | 100 | fastapi | 25625 | 854.2 | 114.4 | 143.4 | 184.3 | 0% |
| users | 100 | nestjs | 33308 | 1110.3 | 79.4 | 148.7 | 220.7 | 0% |

### 错误与资源

- 全部场景错误率为 0；fastapi api 日志无 error 级记录，Granian 未重启 worker，容器未 OOM，healthcheck 全程 healthy。
- 跑完全部档位后，fastapi 两个 worker 进程 RSS 各约 179 MB（存活探针阈值 300 MB）。

## 结论

- 登录：两边都受密码哈希 CPU 成本主导，算法不同（argon2id `t=2,m=19MiB,p=1` 对 bcrypt 10 轮）。fastapi 34–42 RPS，nestjs 12.6–13.5 RPS；100 VU 时 p99 分别为 3.7 s、14.7 s。
- 刷新：fastapi 539–629 RPS，nestjs 733–922 RPS。
- `auth/me`：fastapi 850–924 RPS，nestjs 1246–1357 RPS。
- 用户列表：fastapi 847–868 RPS，nestjs 980–1152 RPS。
- 除登录外，fastapi 吞吐约为 nestjs 的 58–86%，p50 约为 nestjs 的 1.3–1.7 倍。

## 复现

前置：本地有 nestjs-template 仓库（默认 `../nestjs-template`，可用 `NESTJS_DIR` 指定，只读取 `openapi/SOURCE` 中提交的 `env-example-relational`）和 `fastapi-temp/nestjs-ref:02fdb4def2df` 镜像，并装有 `jq`。

```bash
docker build --target production -t fastapi-temp:bench .
docker pull grafana/k6:1.3.0

export WORK_DIR="$(mktemp -d)"
scripts/bench/run.sh up
VU_LEVELS="10 50 100" scripts/bench/run.sh bench
scripts/bench/run.sh down
```

- `up` 生成两份 override 和 nestjs env 到 `WORK_DIR`，以项目名 `bench-fastapi`、`bench-nestjs` 起栈，宿主端口均为 `127.0.0.1` 随机端口。
- `bench` 依次跑完所有组合，每次结果写入 `$WORK_DIR/results/<后端>-<接口>-<VU>.json`，最后输出上面的 Markdown 表；`report` 可单独重新生成表格。
- `down` 以 `down --volumes --remove-orphans` 删除两个栈。
- 单个场景可直接运行 k6，例如：

```bash
docker run --rm --network bench-fastapi_default -v "$PWD/scripts/bench:/scripts:ro" \
  -e BASE_URL=http://api:3000 -e ENDPOINT=me -e VUS=50 -e SUMMARY_FILE=/tmp/me.json \
  grafana/k6:1.3.0 run --quiet /scripts/auth.js
```
