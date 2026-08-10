# project-docs · 项目文档目录

本目录是 AI Project Genesis Workflow 的文档产出区。所有 Agent 产出的项目文档都放在这里，文件名固定，由对应阶段写入，人类负责审核。

## 状态追踪

| 文件 | 产出阶段 | 角色 | 状态 |
|---|---|---|---|
| `00_PROJECT_CONTEXT.md` | Stage 0 | Project Manager | 未开始 |
| `01_PRD.md` | Stage 1 | Product Manager | 未开始 |
| `02_PRD_REVIEW.md` | Stage 2 | Product Owner | 未开始 |
| `03_TDD.md` | Stage 3 | System Architect | 未开始 |
| `04_ARCH_REVIEW.md` | Stage 4 | CTO | 未开始 |
| `05_TASKS.md` | Stage 5 | Engineering Manager | 未开始 |
| `06_DECISIONS.md` | 全程 | 所有角色 | 未开始 |
| `07_TEST_PLAN.md` | Stage 9 | QA Engineer | 未开始 |
| `08_QA_REPORT.md` | Stage 9 | QA Engineer | 未开始 |
| `09_RELEASE.md` | Stage 10 | Release Manager | 未开始 |
| `10_CHANGE_REQUEST.md` | Stage 11 | Change Manager | 未开始 |
| `11_PROJECT_DOD.md` | Stage 10 | Release Manager | 未开始 |

## 使用方式

1. 每个新项目复制整个 `project-docs/` 目录到项目根目录
2. Agent 按阶段填充对应文档，并更新文件头部的状态字段
3. 人类审核通过后，在文档头部记录审核结论与日期
4. 所有决策统一追加到 `06_DECISIONS.md`，编号从 DEC-001 递增

## 状态约定

| 状态 | 含义 |
|---|---|
| 未开始 | 尚未产出 |
| 进行中 | Agent 正在产出 |
| 待审核 | 已产出，等待人工审核 |
| 已通过 | 审核通过，可进入下一阶段 |
| 已完成 | 该阶段全部产出落盘 |
