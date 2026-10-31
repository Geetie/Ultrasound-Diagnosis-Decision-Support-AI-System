# 超声诊疗决策支持 AI 系统

> 多模态（超声图像 + 文本）1+N 多智能体 RAG **辅助诊断**系统（甲状腺 / 肝脏 / 心脏），
> 含融合层（冲突检测 / Self-RAG / CRAG / 分层校验）与独立 SafetyGateway。
>
> **定位是辅助工具：所有输出必须经医生确认，系统不替代医生诊断。**

Agent 的唯一入口是 [AGENTS.md](AGENTS.md)：开工前按其中的 Startup Workflow 取项目现状、
硬规则与细节索引。

## 快速验证

| 命令 | 用途 |
| --- | --- |
| `./init.sh` | harness 统一入口：环境自检 + 后端快测 + ruff 静态检查 |
| `make test` | 后端单元 + INT 集成测试（`-n 8 --dist=loadfile --timeout=300`） |
| `make frontend` | 前端 lint + tsc + vitest + build（阻断） |

pytest **必须带 `--dist=loadfile`**：本仓库多个测试类有链式依赖，按用例随机分发会假失败
（Makefile 已内置该参数）。

## 状态文件

「现在在哪 / 做到哪 / 交接」分别以下面三个文件为唯一来源：

| 文件 | 作用 |
| --- | --- |
| [`progress.md`](progress.md) | 当前状态 / 当前目标 / 下一步、阻塞点——「现在在哪」的唯一来源 |
| [`feature_list.json`](feature_list.json) | feature / 门控状态 + 依赖 + 证据——「做到哪了」的唯一来源 |
| [`session-handoff.md`](session-handoff.md) | 上一会话的阻塞点、涉及文件、下会话第一动作——交接 |
