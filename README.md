# fastapi-temp

FastAPI 后端模板，API 契约与 [nestjs-template](https://github.com/xingyunqi-tech/nestjs-template)（`release/exam-prep`）一致，vite-vue3-temp、uniapp-template 通过 Orval 生成的前端可无改动切换。改造方案见 [TEMPLATE_PLAN.md](TEMPLATE_PLAN.md)。

## 环境

- Python 3.15（`.python-version`），uv 0.13+
- Docker（集成测试、e2e、`make openapi-sync`）

## 常用命令

```bash
make install      # 安装依赖与 Git 钩子
make dev          # 本地开发，http://localhost:3000
make check        # lint + 类型检查 + 单元测试
make test         # 单元 + 集成测试（含覆盖率）
make openapi-sync # 从 nestjs-template 固定提交导出 openapi/ 基线
make help         # 全部目标
```

## OpenAPI

`/docs-json` 逐字节输出 `openapi/nestjs-baseline.json`（nestjs 完整基线，来源见 `openapi/SOURCE`），`/docs` 为 Scalar 页面。生产环境默认关闭（`SWAGGER_ENABLED`）。
