# 秋招简历与面试指南

## 1. 项目定位

推荐项目名：

> 智能座舱多语言语音质量数据分析与治理 Agent

一句话定位：

> 面向七语种智能座舱语音链路，接入 92,301 条 ASR Case 与 104,897 条 NLU 重测汇总两个真实 Provider，构建任务编排、数据分析和数据治理三 Agent 系统，通过受控工具、Hybrid RAG、Grounding、HITL 版本治理和双轨评测实现可验证的数据分析与治理闭环。

项目核心不是“调用大模型聊天”，而是模型、权威数据工具、知识检索和治理状态机的边界设计。

## 2. 推荐简历技术栈

```text
Python / LangGraph / FastAPI / Azure OpenAI / DuckDB / SQLite /
FTS5 / ChromaDB / Hybrid RAG / FastMCP / Pydantic /
LLM-as-Judge / GitHub Actions / Docker / Ruff / Mypy
```

不要写入尚未使用的 Redis、Kubernetes、Celery、Milvus、Azure AI Search、OpenTelemetry 或 Azure Embedding。

## 3. 推荐四条项目经历

```text
- 面向 7 种语言、6 个业务领域，接入 92,301 条 ASR Case 与覆盖 104,897 条样本的 NLU Excel 重测报告两个真实 Provider；设计任务编排、数据分析和数据治理三 Agent 架构，通过 LangGraph 实现结构化理解、多工具并行和跨 Provider 编排。

- 构建插件式 DataProvider + 独立 DuckDB 数据层，统一处理 ASR CSV/JSON 与 NLU Excel/嵌套 JSON；实现 NLU Exact Match Accuracy、190 个 Intent、11,885 条模型错误和 3,501 条数值槽位标签问题分析，并支持 ASR+NLU 同轮联合查询。

- 构建 DuckDB 结构化分析与 SQLite FTS5/BM25 + ChromaDB + RRF Hybrid RAG 双通道查询，结合数字/实体/来源 Grounding；18 条 Retrieval Eval 全部通过，Recall@3 与 MRR 均为 1.0。

- 设计受控 Tool Runtime 与 Data Contract 治理，支持缓存/超时/重试/熔断、ASR Patch Version/Rollback 及 NLU 报告只读治理；建立 GitHub Actions、Ruff、Mypy、74 条测试、25 条核心、62 条银标、7 条 NLU 回归及 Azure LLM-as-Judge 质量门禁。
```

简历篇幅不足时保留前四条，不要再增加“更多 Agent”。

## 4. 可验证指标

| 证据 | 当前结果 | 表述边界 |
|---|---:|---|
| 数据规模 | ASR 92,301 Cases；NLU 104,897 Samples | 两个真实 Provider，7 语言、6 Domain |
| NLU 数据 | 11,885 模型错误、190 Intent、88.67% 修正后准确率 | Excel 是报告，不含全部正确样本明细 |
| 代码测试 | 74/74 | 本地项目 venv |
| 无数据 CI 测试 | 36/36 | GitHub Actions 已远程通过，包含合成 Excel |
| 核心 Agent 回归 | 25/25 | 当前内置确认集，不外推生产准确率 |
| 合成银标 | 62/62 | 模板生成，不是人工金标 |
| NLU/跨 Provider 回归 | 7/7 | 当前报告与内置问题集 |
| Hybrid RAG | 18/18，Recall@3=1.0，MRR=1.0 | 当前内置检索集 |
| Azure smoke | 6/6，Judge 4.7/5 | 在线 smoke，不是生产 SLA |
| 离线 Agent 延迟 | P50 173 ms，P95 1079 ms | 当前机器顺序回归，不是并发压测 |

## 5. 30 秒面试介绍

> 这个项目解决的是多语言智能座舱语音测试数据分散、统计口径不一致和修改不可审计的问题。我把 92,301 条 ASR 明细和覆盖 104,897 条样本的 NLU 重测报告接成两个插件式 Provider，由任务编排 Agent 完成跨 Provider 查询，分析 Agent 调用 DuckDB 和 Hybrid RAG，治理 Agent 处理 ASR 可版本化变更与 NLU 只读标签问题。所有数字由工具计算，回答经过 Grounding，并用确定性评测、LLM-as-Judge、故障注入和 CI 证明效果与可靠性。

## 6. 最值得深挖的面试问题

### 为什么是三个 Agent

任务编排、只读分析和高风险治理具有不同职责与工具权限。回答合成、Grounding 和用户确认是组件或人工节点，不应为展示数量包装成 Agent。

### 如何防止数字幻觉

模型不计算业务指标，只选择注册工具。DuckDB 返回结构化 Observation，Grounding 检查答案中的数字、业务实体、来源范围和真实路径；失败时重组确定性回答。

### RAG 为什么使用 Hybrid

ASR、Domain、Case 编号适合 BM25 精确召回，自然语言改写适合向量召回。项目通过 RRF 融合并用 Recall@3/MRR 评测，而不是凭主观案例判断。

### 模型不可用怎么办

专业 Agent 规划失败使用 Supervisor 已验证 ToolCall；任务编排 Planner 失败使用 OfflinePlanner；Composer 失败使用已有 Observation 的 OfflineComposer。Trace 标记 fallback，工具和数据错误不会被静默吞掉。

### 为什么不用 Redis 或消息队列

当前是受控单机项目，SQLite WAL 和线程池已满足真实边界。只有出现多实例共享状态、后台长任务或高并发时才引入 Redis/队列；提前加入只会增加无法验证的复杂度。

## 7. 现场演示顺序

1. 运行一个跨语言复合问题，展示 Plan、并行 ToolCall、Observation、Replan 和 Grounding。
2. 展示相同工具调用的缓存命中，以及超时、重试、熔断故障测试。
3. 执行治理 Scan → Draft → Diff → Confirm → Publish → Rollback，证明 raw 文件不变。
4. 展示系统页的离线/Azure 双轨评测、Token、成本、P95 和历史运行。
5. 展示 GitHub Actions 工作流及 `scripts/quality_gate.ps1` 的完整门禁。

## 8. 秋招前还需要本人完成的事项

1. 录制 3 到 5 分钟演示视频或 GIF，重点展示 Trace、跨 Provider、治理 Diff 和评测，不录普通聊天过程。
2. 根据目标岗位准备两个版本：算法/Agent 岗强调 RAG、评测和 Grounding；后端/平台岗强调 Provider、Tool Runtime、治理状态机、CI 和故障降级。
3. 本机需要实际运行 Compose 时再安装 Docker Desktop；Dockerfile 已通过 GitHub Actions Linux 构建。

## 9. 暂不建议增加

- 更多没有独立权限或状态的 Agent；
- 没有多实例需求时加入 Redis、Celery 或 Kafka；
- 没有真实对比集时部署 Azure Embedding 或 Reranker；
- 没有组织身份边界时实现 RBAC、双人审批或电子签名；
- 为了技术栈数量引入 Kubernetes；
- 把开发机 P95 写成生产 SLA；
- 把内置评测 100% 写成生产准确率。

这些技术只有在真实需求、可运行实现和可验证指标同时存在时才有简历价值。