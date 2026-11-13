# 压测（k6）

登录、刷新、`auth/me`、用户列表四个接口在同一台机器、同样资源限制下对比 fastapi-temp 与 nestjs-template（`02fdb4d`）。测试日期 2026-11-12。

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
| 并发模型 | `WEB_CONCURRENCY=2`：Granian 2 个 worker 进程，每个进程一个事件循环；密码哈希经 `anyio.to_thread` 放到线程池 | 单个 Node 进程（未启用 cluster），一个事件循环；bcryptjs 为纯 JS 实现，在主线程上分段执行 |
| 密码哈希 | argon2id（`PASSWORD_HASHER=argon2id`，pwdlib 默认参数）：`t=3`、`m=65536`（64 MiB）、`p=4`；种子用户哈希为 `$argon2id$v=19$m=65536,t=3,p=4` | bcryptjs 3.0.3，`genSalt()` 默认 10 轮；种子用户哈希为 `$2b$10$…` |
| 数据库连接池 | 每个 worker `pool_size=20`、`max_overflow=80`（`DATABASE_MAX_CONNECTIONS=100`），两个 worker 合计最多 200 | `DATABASE_MAX_CONNECTIONS=100`，单进程最多 100 |
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
| login | 10 | fastapi | 148 | 4.9 | 1984.8 | 2552.6 | 2896.4 | 0% |
| login | 10 | nestjs | 386 | 12.9 | 752.1 | 989.6 | 1095.9 | 0% |
| login | 50 | fastapi | 84 | 2.8 | 11555 | 16916.3 | 16923.4 | 0% |
| login | 50 | nestjs | 409 | 13.6 | 3556.5 | 3974.6 | 4595.5 | 0% |
| login | 100 | fastapi | 89 | 3 | 18746.9 | 23324.9 | 24532.3 | 0% |
| login | 100 | nestjs | 406 | 13.5 | 5041.3 | 5855.9 | 13623.3 | 0% |
| refresh | 10 | fastapi | 20498 | 683.3 | 14.7 | 22.6 | 33.4 | 0% |
| refresh | 10 | nestjs | 18755 | 625.2 | 14.5 | 25.1 | 57.2 | 0% |
| refresh | 50 | fastapi | 23152 | 771.7 | 74.8 | 113.4 | 143.1 | 0% |
| refresh | 50 | nestjs | 24628 | 820.9 | 61.6 | 100 | 148.8 | 0% |
| refresh | 100 | fastapi | 20882 | 696.1 | 172.4 | 261.4 | 328.2 | 0% |
| refresh | 100 | nestjs | 31573 | 1052.4 | 113.8 | 159.6 | 178.6 | 0% |
| me | 10 | fastapi | 28295 | 943.2 | 10 | 17.2 | 21.7 | 0% |
| me | 10 | nestjs | 41559 | 1385.3 | 6.9 | 9.6 | 12.3 | 0% |
| me | 50 | fastapi | 28752 | 958.4 | 49.4 | 72.1 | 117.4 | 0% |
| me | 50 | nestjs | 47871 | 1595.7 | 30.2 | 40.6 | 55.7 | 0% |
| me | 100 | fastapi | 30981 | 1032.7 | 93.7 | 126.8 | 176.7 | 0.05%（17 个） |
| me | 100 | nestjs | 47147 | 1571.6 | 60.4 | 88.3 | 119.4 | 0% |
| users | 10 | fastapi | 26724 | 890.8 | 11.9 | 19.1 | 23.1 | 0% |
| users | 10 | nestjs | 37209 | 1240.3 | 7.7 | 10.3 | 13.7 | 0% |
| users | 50 | fastapi | 28047 | 934.9 | 51.5 | 69.4 | 108.2 | 0% |
| users | 50 | nestjs | 43112 | 1437.1 | 33.6 | 44.9 | 62.4 | 0% |
| users | 100 | fastapi | 28951 | 965 | 103.9 | 141 | 184 | 0.04%（13 个） |
| users | 100 | nestjs | 42626 | 1420.9 | 67.6 | 95.4 | 124.9 | 0% |

### 非零错误与异常

- **fastapi `me`/`users` 100 VU 的错误**：均为 500，api 日志为 `asyncpg.exceptions.TooManyConnectionsError: sorry, too many clients already`。两个 Granian worker 各自的连接池上限合计 200（20 + 80 溢出，每进程），而 Postgres `max_connections=100`，且 worker、scheduler 也占用连接。nestjs 单进程连接池上限 100，未出现该错误。
- **fastapi 登录 50/100 VU 吞吐低于 10 VU**：argon2id 每次哈希需 64 MiB，线程池中并发的哈希使 api 容器内存达到 1 GiB 上限（cgroup `memory.events` 中 `max` 计数 10342），超出部分进入 swap（测后 `memory.swap.current` 约 349 MiB，`pgmajfault` 约 70 万次）。此时 CPU 仍为 200%，但吞吐从 4.9 降到约 3 RPS，p50 升到 11–19 s。
- **fastapi 登录 50 VU 复跑时 api 退出**：在全部档位跑完后单独复跑 fastapi 登录 50 VU，一个 Granian worker 被 OOM kill（容器 `OOMKilled=true`），Granian 随即关闭全部 worker 并退出（日志 `Unexpected exit from worker-1` → `Granian shutdown completed`），api 容器无 restart 策略，之后请求全部失败。该次结果未计入上表。
- **fastapi 存活探针在登录压测中返回 503**：`/api/v1/health/live` 以 worker 进程 RSS > 300 MB 判为 down，登录 10 VU 期间记录到 2 次 `memory_heap` down；容器 healthcheck 未达到连续失败阈值。

## 结论

- 登录：两边都受密码哈希 CPU 成本主导，且算法不同（argon2id `t=3,m=64MiB,p=4` 对 bcrypt 10 轮）。10 VU 时 fastapi 4.9 RPS、nestjs 12.9 RPS；50、100 VU 时 nestjs 稳定在约 13.5 RPS，fastapi 因 1 GiB 内存限制下的 swap 降到约 3 RPS，复跑时出现 OOM 退出。
- 刷新：10 VU 时 fastapi 683 RPS、nestjs 625 RPS；50 VU 时分别为 772、821 RPS；100 VU 时分别为 696、1052 RPS。
- `auth/me`：各档 fastapi 943–1033 RPS，nestjs 1385–1596 RPS；fastapi p50 约为 nestjs 的 1.4–1.6 倍。
- 用户列表：各档 fastapi 891–965 RPS，nestjs 1240–1437 RPS；fastapi p50 约为 nestjs 的 1.5 倍。
- fastapi 在 100 VU 下 `me`、`users` 出现 Postgres 连接数超限的 500；nestjs 全部场景错误率为 0。

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
