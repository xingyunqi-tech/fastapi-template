# 压测（k6）

登录、`me`、用户列表、notes 列表四个接口在单机、固定资源限制下的吞吐与延迟。测试日期 2026-11-24。

## 环境

| 项 | 值 |
|---|---|
| 机器 | MacBook Pro（MacBookPro18,2），Apple M1 Max，10 核，32 GB |
| Docker | Docker Desktop，Engine 28.5.1，VM 分配 10 CPU、7.75 GiB 内存 |
| 压测工具 | `grafana/k6:1.3.0`，容器接入被测栈的 compose 网络，直连 `http://api:3000` |
| 服务栈 | `docker/docker-compose.{base,postgres,redis,ci}.yaml` 加 `scripts/bench/run.sh` 生成的 override，项目名 `bench-fastapi` |
| 镜像 | `fastapi-temp:bench`（`Dockerfile` 的 `production` target）：Python 3.15.2、Granian 2.8.7（ASGI） |
| api 容器限制 | `cpus: 2`、`mem_limit: 1g`；Postgres、Redis、worker、scheduler、mailpit 不限 |
| 并发模型 | `WEB_CONCURRENCY=1`（`OTEL_METRICS_EXPORTER=prometheus` 时只能为 1）：单个 Granian worker、一个事件循环；密码哈希在线程池执行，同时最多 2 个（`PASSWORD_HASH_CONCURRENCY=2`） |
| 密码哈希 | argon2id，`m=19456`（19 MiB）、`t=2`、`p=1` |
| 数据库连接池 | `DATABASE_POOL_SIZE=10` |
| Postgres / Redis | `postgres:18.6-alpine`、`redis:8.10.3-alpine` |
| 模式 | `APP_ENV=production`（ci overlay），info 级 JSON 日志，每个请求一条访问日志 |
| 限流 | 共享规则照常生效；私有默认规则 `RATE_LIMIT_DEFAULT=1/ms,burst=100000000`，仍每次请求访问 Redis |

## 方法

- 账号：种子管理员 `admin@example.com` / `Secret00`。
- 接口：
  - `POST /api/v1/auth/email/login`：每次请求带随机的 `X-Forwarded-For`（`TRUSTED_PROXIES=1`），避开按 IP 计数的共享规则 `auth-login`。
  - `GET /api/v1/me`：setup 阶段登录一次，所有 VU 共用同一个 access token。
  - `GET /api/v1/users?pageSize=10`：同上。
  - `GET /api/v1/notes?pageSize=10`：同上，setup 阶段先创建 20 条笔记。
- `refreshToken` 不压测：共享规则 `auth-refresh` 按会话限制为 30 次 / 分钟。
- 每个场景单独跑一次 k6：`constant-vus`，总时长 40 s，前 10 s 为预热，只统计之后 30 s 内发出的请求（按 `phase` 标签过滤）；VU 无思考时间。
- VU 分 10、50、100 三档，按档位、接口顺序执行。
- 指标：RPS = 统计窗口内完成请求数 / 30 s；p50、p95、p99 为 `http_req_duration`；错误率为 `http_req_failed`（非 2xx/3xx 或网络错误）。

## 结果

| 接口 | VU | 请求数 | RPS | p50 (ms) | p95 (ms) | p99 (ms) | 错误率 |
|---|---|---|---|---|---|---|---|
| login | 10 | 1106 | 36.9 | 258.2 | 364 | 450 | 0% |
| login | 50 | 1074 | 35.8 | 1362.4 | 1744.3 | 2016.3 | 0% |
| login | 100 | 1027 | 34.2 | 2821.7 | 3409.2 | 3652.1 | 0% |
| me | 10 | 11099 | 370 | 24.8 | 35.1 | 78.6 | 0% |
| me | 50 | 9588 | 319.6 | 145.4 | 224.6 | 318.8 | 0% |
| me | 100 | 9589 | 319.6 | 298.7 | 397.3 | 587.9 | 0% |
| users | 10 | 8990 | 299.7 | 32.2 | 39.9 | 79.4 | 0% |
| users | 50 | 7457 | 248.6 | 171.4 | 427.3 | 659.3 | 0% |
| users | 100 | 8356 | 278.5 | 341.3 | 672.2 | 920 | 0% |
| notes | 10 | 9911 | 330.4 | 28.6 | 36.7 | 78.3 | 0% |
| notes | 50 | 9322 | 310.7 | 151.1 | 218.5 | 292.2 | 0% |
| notes | 100 | 9410 | 313.7 | 308.4 | 376.4 | 434.3 | 0% |

- 登录受密码哈希 CPU 成本主导，34–37 RPS，延迟随 VU 线性增长。
- 带令牌的读接口在 10 VU 时已接近单进程上限（300–370 RPS），继续加 VU 只增加排队延迟。
- 全部场景错误率为 0。

## 复现

前置：Docker、`jq`、`grafana/k6:1.3.0` 镜像。

```bash
make bench                                   # = scripts/bench/run.sh all：构建镜像、起栈、跑全部场景、输出表格、删除栈
```

分步执行：

```bash
export WORK_DIR="$(mktemp -d)"
scripts/bench/run.sh up                      # 构建 fastapi-temp:bench，生成 override，起栈
VU_LEVELS="10 50" ENDPOINTS="me notes" scripts/bench/run.sh bench
scripts/bench/run.sh report                  # 由 $WORK_DIR/results 重新生成表格
scripts/bench/run.sh down                    # down --volumes --remove-orphans
```

- 变量：`VU_LEVELS`、`ENDPOINTS`、`API_CPUS`、`API_MEM`、`BENCH_IMAGE`、`WORK_DIR`、`OUT_DIR`，默认值见 `scripts/bench/run.sh` 开头。
- 每个场景的结果写入 `$OUT_DIR/<接口>-<VU>.json`。
- 单个场景可直接运行 k6：

```bash
docker run --rm --network bench-fastapi_default -v "$PWD/scripts/bench:/scripts:ro" \
  -e BASE_URL=http://api:3000 -e ENDPOINT=me -e VUS=50 -e SUMMARY_FILE=/tmp/me.json \
  grafana/k6:1.3.0 run --quiet /scripts/api.js
```
