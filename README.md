# 智能座舱多语言语音质量数据分析与治理 Agent

[![Quality Gate](https://github.com/wellercv/intelligent-cockpit-data-agent/actions/workflows/quality.yml/badge.svg)](https://github.com/wellercv/intelligent-cockpit-data-agent/actions/workflows/quality.yml)

英文名称：**Intelligent Cockpit Multilingual Voice Quality Data Agent**。

项目简称：**智能座舱语音质量 Agent**。

项目面向智能座舱语音交互质量场景，接入七语种车载 ASR 测试明细与离线 NLU 重测报告两个真实业务 Provider。任务编排 Agent（Supervisor Agent）统一负责首轮意图与实体理解、任务拆解、专业 Agent 分派和基于 Observation 的重规划；数据分析 Agent（Data Analysis Agent）负责只读调查，数据治理 Agent（Data Governance Agent）负责质量问题和变更生命周期。回答合成、事实校验、人工确认和版本发布是确定性组件或人工节点，不额外包装成 Agent。

## 一屏概览

![智能座舱语音质量 Agent 总览](docs/diagrams/vehicle_quality_agent_overview.png)

| 维度 | 当前可验证结果 |
|---|---|
| 真实数据 | ASR 92,301 Cases；NLU 104,897 Samples；均覆盖 7 种语言、6 个 Domain |
| Agent 架构 | 任务编排、数据分析、数据治理三 Agent；最多三轮 Observation 驱动重规划 |
| 事实与知识 | DuckDB 精确计算 + FTS5/BM25、ChromaDB、RRF Hybrid RAG |
| 可信与治理 | 数字/实体/来源 Grounding；Diff 确认、不可变 Patch Version、Rollback |
| 可靠性 | Tool Runtime 缓存/超时/重试/熔断；Azure Planner/Composer 确定性 fallback |
| 评测 | 78/78 测试、25/25 核心、62/62 银标、7/7 NLU、18/18 Retrieval、Azure smoke Judge 4.7/5 |
| 工程门禁 | GitHub Actions、Ruff、Mypy、pip-audit、Dependabot、Docker Demo 健康检查 |

## 设计文档

| 文档 | 用途 |
|---|---|
| [中文项目总说明书](docs/PROJECT_MASTER_GUIDE_CN.md) | 背景、动机、目标、完整设计、技术知识解释、效果、路线、领导汇报、简历和面试讲法 |
| [中文项目总说明书 Word 版](docs/PROJECT_MASTER_GUIDE_CN.docx) | 可直接阅读、评审或发送的排版版本 |
| [需求设计](docs/REQUIREMENTS_DESIGN.md) | 用户、范围、功能、非功能需求、业务规则和验收标准 |
| [原型设计](docs/PROTOTYPE_DESIGN.md) | 页面信息架构、线框、交互流程、状态和响应式规范 |
| [架构设计](docs/ARCHITECTURE_DESIGN.md) | 系统上下文、组件、数据流、接口、安全、部署和演进 |
| [三业务 Agent 设计](docs/BUSINESS_AGENT_DESIGN.md) | Supervisor、Analysis、Data Governance、人工确认和版本治理 |
| [Azure LLM 接入教程](docs/LLM_API_SETUP.md) | 从申请配置到在线评测的操作指南 |
| [业务素材接入指南](docs/MATERIALS_GUIDE.md) | 数据契约、知识文档、业务规范和评测集的接入方式 |
| [秋招简历与面试指南](docs/RESUME_GUIDE_CN.md) | 可验证简历表述、项目介绍、面试深挖和禁止过度承诺项 |
| [安全策略](SECURITY.md) | 漏洞报告方式、当前安全控制和临时依赖风险接受范围 |

修改总说明书后可重新生成 Word：

```powershell
python docs/build_project_docx.py
```

它不是让大模型直接读取或修改文件，而是让任务编排 Agent 判断进入只读分析还是数据治理流程。数据分析 Agent 查询真实数据；数据治理 Agent 按数据契约发现问题、创建变更草稿；用户检查结构化 Diff 和契约结果并显式确认后，系统生成不可变补丁版本、重建分析仓库并支持回滚。

```text
问题 -> 计划 -> 执行 -> 观察 -> 重规划 -> 事实校验 -> 回答
```

## 当前数据

- 7 种语言：Arabic、English、French、German、Italian、Portuguese、Spanish
- 6 个 domain：carControl、generalControl、mediaControl、naviControl、phone、systemControl
- 42 组 CSV + JSON
- 92,301 条 CSV Case
- 5,118 条 CSV 错误，1 条 Result 未知（不再默认计为错误）
- 3 个导入级 warning
- 44 个契约与跨来源治理候选：1 error、41 warning、2 info
- 1 份离线 NLU 重测 Excel：104,897 条样本、93,012 条修正后正确、11,885 条模型错误
- NLU 修正标签口径后的 Exact Match Accuracy：88.67%
- NLU 错误明细：4,222 Intent、4,039 Slots、3,086 Domain、514 Language、24 Parse Failure
- NLU 标签质量：3,501 条数值槽位问题、14,889 条受语言命名口径影响；治理扫描归并为 49 个候选

原始数据保持在项目同级目录，平台不会修改它们。`config/sources.yaml` 注册 Provider，`config/contracts/*.yaml` 定义主键、必填、类型、枚举、范围、唯一性和可修改字段。ASR Provider 使用 CSV/JSON 与补丁版本；NLU Provider 使用 Excel 汇总、标签问题和模型错误明细，报告本身保持只读。两个 Provider 共享 Agent Engine、Tool Runtime、Grounding、FastMCP、评测与治理状态机。

## Multi-Agent 能力

- 基于 LangGraph 的任务编排状态图
- 任务编排 Agent：输出结构化意图、语言/领域/指标/Case/来源实体，判断进入分析、治理或混合流程
- 数据分析 Agent：统一负责指标、记录调查和知识检索，只读
- 数据治理 Agent：负责契约扫描、治理问题、变更草稿，以及包含 before/after 和契约校验结果的结构化差异预览
- 分析/治理工具白名单和越权阻断
- 分析与治理任务可并行执行，结果由非 Agent 的回答合成组件汇总
- 最小 HITL：用户检查 Before/After Diff 与 Data Contract 结果后显式确认；无需企业身份、RBAC 或独立审批人
- 原始文件永不覆盖；活动补丁层可回滚
- 离线确定性 Planner 与可选 Azure OpenAI Planner
- 多工具并行执行和最多三轮重新规划
- 插件式 Data Provider
- ASR + NLU 跨 Provider 并行分析，不新增业务 Agent
- 通用 Data Contract 与 Provider-neutral Contract Scanner
- DuckDB 多维指标分析
- Case 关键词和字段检索
- SQLite FTS5/BM25 + ChromaDB 向量检索 + RRF 融合的 Hybrid RAG
- 离线特征哈希 Embedding；配置 Azure `text-embedding-3-small` 后自动切换在线 Embedding
- SQLite 工作记忆与 ChromaDB 调查记忆
- 基于官方 FastMCP 的 MCP 工具服务（确认、发布、回滚不向模型暴露）
- 确定性意图/实体/工具/Agent/答案/引用评测 + 可选 Azure LLM-as-Judge 双轨评测
- Hybrid RAG Recall@K / MRR 专项评测与最低晋级门槛
- SQLite 持久化 Trace
- 运行时 Skills 加载
- Tool Runtime：60 秒 TTL 缓存、10 秒超时、1 次重试、连续 3 次失败熔断 30 秒、最多 8 工作线程
- Azure Planner/Composer 异常时确定性降级，并在 Trace 中记录 fallback 类型
- 数字、关键业务实体、来源范围与来源路径 Grounding
- FastAPI、Swagger、Streamlit 和 CLI
- 端到端 Agent 评测与结果持久化
- 62 条可重复生成的合成银标评测；与 25 条人工确认核心集分开报告
- 知识 Markdown 支持来源、验证日期、可信度、敏感级别和 `index: false` 元数据

当前不包含 TN 规则、错误自动分诊、Experiment Agent 或 Reporting Agent。后两者只有在获得第二批版本数据或正式报告签审需求后才值得增加。

## 零数据快速演示

公开仓库不包含真实业务原始数据。首次克隆后可直接生成确定性合成 fixture，运行与真实模式相同的 Agent、Provider、RAG、Grounding、治理和 Trace 链路：

```powershell
git clone https://github.com/wellercv/intelligent-cockpit-data-agent.git
cd intelligent-cockpit-data-agent
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
data-agent demo
```

打开 <http://127.0.0.1:8501>。页面会显著显示 **Synthetic Demo**，当前 fixture 为 42 条 ASR Case 和 14 条 NLU Sample；这些数字只验证系统能力，不能作为业务结果或简历效果指标。

## 真实数据运行

建议 Python 3.11+。

```powershell
cd C:\Users\t-wcui\Downloads\ASR_agent\data_insight_agent
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

导入或刷新数据：

```powershell
data-agent ingest
```

真实模式必须保持 `DATA_AGENT_DEMO_MODE=0`，并通过 `DATA_AGENT_DATA_ROOT`、`DATA_AGENT_NLU_REPORT` 或 `config/sources.yaml` 指向授权数据。

运行测试：

```powershell
python -m pytest -q
```

运行端到端 Agent 评测：

```powershell
data-agent eval
```

创建或确认评测基线：

```powershell
# 仅在确认当前版本结果正确后更新基线
data-agent eval --update-baseline

# 后续运行会自动与基线比较；核心指标下降超过 5% 时返回失败
data-agent eval
```

当前代码测试：`78/78 passed`。核心 Agent 离线评测：`25/25 passed`；另有 `62/62` 合成银标和 `7/7` NLU/跨 Provider 用例通过。NLU 回归覆盖整体指标、语言/Domain 排名、错误分布、错误明细、标签治理和 ASR+NLU 联合查询，Intent、Entity、Tool、Agent、Answer 和 Citation Accuracy 均为 `100%`。银标由模板生成，不计作人工标注，也不代表生产准确率。

Azure `gpt-5.4-mini` 已通过 Microsoft Entra ID 完成真实在线验收：历史完整回归的 25 条确定性指标全部通过；当前 6 条低成本 smoke 全部通过，LLM-as-Judge 平均 `4.7/5`、无策略违规、24 次调用、119,181 tokens、P95 `6004 ms`、估算 `$0.123`。历史完整回归未持久化可恢复的 Judge 分，因此不为它补写 Judge 结论。Azure Embedding 未部署且不是当前阻塞项；离线 Hybrid RAG 已通过 18 条专项评测。

当前 Hybrid RAG 专项评测：`18/18 passed`，Recall@3 和 MRR 均为 `1.0`，通过 Recall@3 `>= 0.90`、MRR `>= 0.75` 的最低门槛（当前内置检索集）。

当前机器上的 25 条顺序离线回归基线：Agent 端到端 P50 `173 ms`、P95 `1079 ms`。这是开发机回归数据，不是并发压测、线上 SLA 或可用性承诺。

## 工程质量门禁

项目采用两层验证，避免公开 CI 依赖或复制私有业务原始数据：

- GitHub Actions：Ruff、Mypy 核心类型检查、40 条无业务数据测试、依赖一致性、`pip-audit` 和 Docker Demo 启动/健康检查；
- 本地全量门禁：Ruff、Mypy、代码测试、25 条核心 Agent 回归、62 条合成银标回归、7 条 NLU 回归、依赖一致性和漏洞扫描，严格串行执行以隔离本地治理状态；
- Dependabot：每周检查 Python 与 GitHub Actions 依赖更新。

```powershell
.\scripts\quality_gate.ps1
```

只运行静态检查和 78 条代码测试：

```powershell
.\scripts\quality_gate.ps1 -SkipEvaluations
```

## 使用

### Web 工作台

```powershell
data-agent ui
```

打开 <http://127.0.0.1:8501>。

工作区包括：

- **分析工作台**：自然语言提出分析目标，查看结果、Observation 和来源，并下载完整 JSON 或展平后的 CSV；
- **数据治理**：扫描治理问题、创建变更草稿、预览结构化 Diff、用户显式确认、发布版本和回滚；
- **Agent Trace**：查看 Plan、Tool、Replan、Verify、Answer；
- **系统与评测**：Provider、Skills、Tool Runtime、成本/P95、分级端到端评测和历史运行。Azure 默认运行 6 条 smoke，25 条完整回归需显式确认费用。

### CLI

```powershell
data-agent ask "七种语言中哪个错误率最高？"
data-agent ask "比较德语和法语 mediaControl 的错误率，并分别列出各自 3 条错误案例"
data-agent ask "法语 carControl 的 CSV 和 JSON 口径有什么差异？"
data-agent ask "准确率指标是怎么计算的？"
```

### FastAPI

```powershell
data-agent serve
```

- Swagger：<http://127.0.0.1:8000/docs>
- Health：`GET /health`
- Chat：`POST /chat`
- Tools：`GET /tools`
- Trace：`GET /traces/{trace_id}`
- Skills：`GET /skills`
- Reload Skills：`POST /skills/reload`
- Monitor：`GET /monitor`
- Evaluation：`POST /eval/run`
- Retrieval Evaluation：`GET /eval/retrieval`
- NLU Overview：`GET /nlu/overview`
- NLU Errors：`GET /nlu/errors`、`GET /nlu/errors/{error_id}`
- NLU Label Quality：`GET /nlu/label-quality`
- Investigation Memory：`GET /memory/investigations?query=...`
- Governance：`/governance/scan`、`/governance/issues`、`/governance/changes`、`/governance/publish`、`/governance/rollback`

示例：

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/chat" `
  -ContentType "application/json" `
  -Body '{"question":"七种语言中哪个错误率最高？"}'
```

### MCP 工具服务

本地 IDE/Agent 客户端使用标准输入输出：

```powershell
data-agent mcp --transport stdio
```

远程客户端使用 Streamable HTTP：

```powershell
data-agent mcp --transport streamable-http --host 127.0.0.1 --port 8001
```

MCP 地址：`http://127.0.0.1:8001/mcp`。

## Azure OpenAI

不配置模型也能运行完整离线 Agent。配置 Azure 后，Planner 可以处理更自由的复杂问题，Composer 可以根据 Observation 组织更自然的回答。完整新手教程见 [docs/LLM_API_SETUP.md](docs/LLM_API_SETUP.md)。

```powershell
data-agent llm init
# 在本机编辑 .env，填写 Endpoint、Chat Deployment，并选择 Key 或 Entra 认证
# 推荐底层模型 gpt-5.4-mini；设置 API_MODE=v1、REASONING_EFFORT=low
# 可选填写 AZURE_OPENAI_EMBEDDING_DEPLOYMENT（推荐 text-embedding-3-small）
data-agent llm login  # Entra 模式仅首次或会话过期时运行
data-agent llm status
data-agent llm test
data-agent ask "分析七语种中最值得关注的问题" --mode azure
data-agent eval --mode azure
```

真实 Azure 评测确认正确后，可单独创建 Azure 基线：

```powershell
data-agent eval --mode azure --update-baseline
```

不要在第一次模型试跑时直接更新基线；先人工检查 Agent Trace、工具参数和回答来源。

大模型只负责问题理解、规划、重规划和表达；所有业务数字仍由工具计算。Azure 回答若未通过 Grounding 校验，系统自动降级为确定性回答模板。

## 数据与知识

```text
config/sources.yaml       原始数据源注册
config/contracts/         Provider 数据契约
knowledge/*.pdf|*.docx    原始参考附件，仅留存，不直接索引
knowledge/**/*.md         带来源元数据的指标、口径和脱敏业务知识
skills/                   Agent 操作规范，可运行时重载
eval/datasets/            核心问题、合成银标和 Retrieval 期望行为
eval/generate_synthetic_dataset.py  可重复生成银标集
data/warehouse.duckdb     自动生成的分析仓库
data/nlu_report.duckdb    自动生成的 NLU 报告仓库
data/agent_state.db       会话、Trace、治理、确认、版本和评测结果
data/knowledge_chroma/    ChromaDB 业务知识向量索引
data/investigation_chroma/ ChromaDB 历史调查记忆
```

新增正式资料时参见 [docs/MATERIALS_GUIDE.md](docs/MATERIALS_GUIDE.md)。

## Docker

在仓库目录运行。Compose 默认启动明确标注的 Synthetic Demo：

```powershell
docker compose up --build
```

- API：<http://127.0.0.1:8000/docs>
- UI：<http://127.0.0.1:8501>

切换真实数据前，在 PowerShell 中设置：

```powershell
$env:DATA_AGENT_DEMO_MODE="0"
docker compose up --build
```

Compose 将上一级数据目录只读挂载到 `/workspace`，原始数据不会被容器修改。

Dockerfile 由 GitHub Actions 在 Linux runner 完成镜像构建，并实际启动容器校验 `/health` 中的 Demo ASR/NLU 指标；本机只有需要运行 Compose 时才需要安装 Docker Desktop。
