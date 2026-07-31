# 智能座舱多语言语音质量数据分析与治理 Agent：需求设计

> 文档类型：产品需求与软件需求规格说明（PRD / SRS）  
> 产品名称：智能座舱多语言语音质量数据分析与治理 Agent  
> 英文名称：Intelligent Cockpit Multilingual Voice Quality Data Agent  
> 当前版本：0.1.0  
> 首个业务数据源：七语种车载 ASR 测试数据  
> 文档状态：评审稿，描述当前已实现 MVP 及下一阶段需求

---

## 1. 文档目的

本文定义平台解决的问题、目标用户、产品边界、功能需求、非功能需求、业务规则和验收标准。它是原型设计和架构设计的上游依据。

状态说明：

| 状态 | 含义 |
|---|---|
| `已实现` | 当前代码、界面和测试中已经存在 |
| `待在线验收` | 代码已实现，但依赖真实 Azure 凭据或生产环境验证 |
| `下一阶段` | 已纳入路线，但当前版本尚未实现 |
| `不在范围` | 当前产品主动不做 |

---

## 2. 项目背景

七种语言的 ASR 测试结果分散在 42 份 CSV 和 42 份 JSON 中。分析人员通常需要：

1. 手工定位语言和 domain 文件；
2. 计算总数、错误数、准确率和错误率；
3. 对比多种语言或多个 domain；
4. 查找 REF/HYP 和具体 Case；
5. 解释 CSV 与 JSON 的数据口径；
6. 整理结果和原始来源；
7. 发现质量问题后提交、审批和追踪修正；
8. 重复回答相似的数据问题。

现有方式存在以下问题：

- 文件数量多，跨语言查询步骤重复；
- 数据口径可能不同，人工容易混用；
- 普通大模型无法可靠统计 92,301 条记录；
- 单纯搜索只能找到文本，不能完成精确计算；
- 固定 BI 看板难覆盖临时组合式问题；
- 分析过程缺少来源、执行轨迹和回归评测。
- 数据修正缺少 Diff、职责分离、版本和回滚。

---

## 3. 产品定位

### 3.1 一句话定位

> 用户提出业务数据分析或治理目标，由任务编排 Agent 调度只读数据分析 Agent 与数据治理 Agent，交付可追溯结论或经过审批、版本化且可回滚的数据变更。

### 3.2 产品类型

本产品是**智能座舱语音质量数据分析与治理 Agent**，不是智能客服。

- 自然语言问答是交互方式；
- 数据分析、契约扫描、受控变更和任务规划是核心能力；
- 当前服务对象是分析、测试、业务、数据 Owner 和管理人员；
- 当前不处理订单、退款、工单、人工转接和客服 SLA。

### 3.3 当前业务落地

首个数据源接入七语种车载 ASR 测试数据：

| 维度 | 当前范围 |
|---|---:|
| 语言 | 7 |
| Domain | 6 |
| CSV | 42 |
| JSON | 42 |
| CSV Case | 92,301 |
| CSV 错误 | 5,118 |
| Result 未知 | 1 |
| 治理扫描候选 | 44 |

### 3.4 平台化边界

Agent Engine 与业务文件格式解耦。当前已完成 ASR CSV/JSON Provider 与 NLU Excel Evaluation Provider 两个真实数据源的端到端接入；二者共享 Agent Engine、Tool Runtime、Grounding 和治理状态机，并分别提供 Data Contract 与 Provider-specific Adapter。

---

## 4. 产品目标与成功标准

### 4.1 业务目标

1. 降低跨语言数据查询和整理成本；
2. 统一指标计算和来源口径；
3. 让非 SQL 用户通过自然语言完成常用分析；
4. 让每个数字、案例和结论可追溯；
5. 建立可回归、可观察的 Agent 工程主链；
6. 对数据修正执行治理问题登记、结构化差异预览、用户显式确认、版本发布和回滚；
7. 为后续接入第二类业务数据提供契约与插件边界。

### 4.2 当前可验证目标

| 指标 | 当前验收目标 | 当前结果 |
|---|---:|---:|
| CSV Case 导入完整性 | 92,301 | 92,301 |
| 语言覆盖 | 7 | 7 |
| Domain 覆盖 | 6 | 6 |
| 代码自动测试 | 全部通过 | 74/74 |
| NLU 报告导入完整性 | 104,897 汇总 + 11,885 错误明细 | 通过 |
| NLU Provider 回归集 | 全部通过 | 7/7 |
| 核心 Multi-Agent 回归集 | 全部通过 | 25/25 |
| 合成银标 Agent 回归集 | 全部通过并单独报告 | 62/62 |
| Intent Accuracy | 100%（25 条当前标注意图） | 100% |
| Entity Accuracy | 100%（18 条当前标注关键实体） | 100% |
| Tool Accuracy | 100%（当前回归集） | 100% |
| Agent Accuracy | 100%（当前回归集） | 100% |
| Answer Accuracy | 100%（当前回归集） | 100% |
| Citation Accuracy | 100%（当前回归集） | 100% |
| Hybrid RAG 检索集 | 全部通过 | 18/18 |
| Retrieval Recall@3 | 不低于 0.90 | 1.0 |
| Retrieval MRR | 不低于 0.75 | 1.0 |
| 相对离线基线回归 | 无超过 5% 的退化 | 无回归 |

以上结果只适用于当前回归集，不能外推为任意自然语言问题准确率 100%。

### 4.3 下一阶段业务指标

取得真实在线模型凭据和脱敏生产日志样本后，应补充：

- Azure Tool Accuracy；
- Azure Intent/Entity Accuracy；
- Azure Answer/Citation Accuracy；
- 平均模型调用次数、token 和延迟；
- 复杂问题任务完成率；
- 人工分析平均耗时降低比例；
- 用户对回答可用性的评分。

---

## 5. 用户角色

### 5.1 数据分析与测试人员

目标：快速查询指标、对比语言、定位案例并核对来源。

典型问题：

- 七种语言中哪个错误率最高？
- 比较德语和法语 mediaControl 的错误率。
- 找出英语中包含 TuneIn 的错误案例。

### 5.2 业务负责人或项目经理

目标：快速理解整体表现和高风险范围，并获得可用于汇报的证据。

典型问题：

- 哪个语言或 domain 最值得关注？
- 法语 CSV 和 JSON 为什么数字不同？
- 当前数据有哪些质量问题？

### 5.3 Agent 开发与运维人员

目标：检查 Planner、工具参数、来源、模型调用、评测和回归状态。

典型操作：

- 查看 Agent Trace；
- 查看 Tool Runtime 指标；
- 测试 Azure 连接；
- 运行 25 条端到端评测；
- 对比评测基线。

### 5.4 平台扩展开发者

目标：接入新的业务数据 Provider，而不修改 Agent Engine。

---

## 6. 核心用户旅程

### UC-01：整体指标查询

**触发**：用户询问当前接入数据总量或整体表现。  
**前置条件**：Warehouse 已完成导入。  
**主流程**：

1. Agent 识别为整体指标问题；
2. 调用 `dataset_overview`；
3. DuckDB 计算总量、正确、错误及按语言分布；
4. Composer 生成表格；
5. Grounding 校验数字和来源；
6. 返回回答和 Trace。

**验收**：总数为 92,301，错误数为 5,118，Result 未知为 1，来源可打开。

### UC-02：跨语言复合分析

**触发**：用户要求比较指标并列出案例。  
**主流程**：

1. 任务编排 Agent 委派给数据分析 Agent；
2. 数据分析 Agent 调用 `compare_languages` 和多个 `search_cases`；
3. 工具在数据分析 Agent 内并行执行；
4. AnswerSynthesizer 合并指标和案例；
5. Grounding 校验并返回。

**验收**：Trace 中出现任务编排、数据分析、并行工具、校验和回答合成事件。

### UC-03：多轮上下文分析

**对话**：

```text
用户：法语准确率是多少？
用户：这个语言哪个 domain 错误率最高？
```

**验收**：第二轮继承 French；如果用户明确指定新语言，则新语言覆盖历史上下文。

### UC-04：数据口径对比

**触发**：用户同时提及 CSV 与 JSON 或询问来源口径。  
**验收**：系统分别展示两种口径，不静默合并，不在缺少证据时推测原因。

### UC-05：知识定义查询

**触发**：用户询问准确率、错误率或数据范围定义。  
**验收**：调用 `search_knowledge`，引用 `knowledge/` 中的真实文档，不使用模型常识替代项目定义。

### UC-06：越界拒答

**触发**：天气、销售报告、未接入数据或 Prompt 越界请求。  
**验收**：调用 `platform_capabilities`，说明支持范围，不生成无来源答案。

### UC-07：Agent 诊断

**触发**：开发者查看 Trace 或系统状态。  
**验收**：可以看到规划、工具参数、Observation、验证结果、工具延迟、模型 token 和评测状态。

### UC-08：Azure 接入

**主流程**：

1. 运行 `data-agent llm init`；
2. 用户仅在本机 `.env` 填写配置；
3. `llm status` 做无网络检查；
4. `llm test` 发送最小请求；
5. Azure 模式出现在前端；
6. 运行简单问题、复合问题和 Azure 评测。

**安全要求**：不得在界面、日志、Trace 或文档中展示 API Key。

---

## 7. 功能需求

### 7.1 数据接入与治理

| ID | 需求 | 优先级 | 状态 |
|---|---|---:|---|
| FR-DATA-001 | 通过配置注册语言目录和文件模式 | P0 | 已实现 |
| FR-DATA-002 | 导入 CSV Case 到统一 DuckDB Schema | P0 | 已实现 |
| FR-DATA-003 | 导入 JSON 运行汇总并保留独立口径 | P0 | 已实现 |
| FR-DATA-004 | 使用源文件指纹避免无变化时重复导入 | P1 | 已实现 |
| FR-DATA-005 | 记录列数异常和 CSV/JSON 总量不一致 | P0 | 已实现 |
| FR-DATA-006 | 不修改原始数据文件 | P0 | 已实现 |
| FR-DATA-007 | 使用 Data Contract 定义主键、必填、类型、枚举、范围、唯一性和 mutable | P0 | 已实现 |
| FR-DATA-008 | 使用通用 Contract Scanner 检查不同业务记录 | P0 | 已实现，已用销售数据测试 |
| FR-DATA-009 | 通过 Governance Adapter 扩展业务特有规则 | P0 | 已实现 ASR Adapter |
| FR-DATA-010 | 接入第二类正式非 ASR 业务 Provider | P1 | 已完成 NLU Excel Evaluation Provider |
| FR-DATA-012 | NLU 报告汇总、标签问题和模型错误明细按文件指纹导入独立 DuckDB | P1 | 已实现 |
| FR-DATA-013 | NLU Excel 缺失时 Provider 降级而不阻断 ASR 主链 | P1 | 已实现并测试 |
| FR-DATA-011 | 支持版本、日期和模型维度的趋势分析 | P2 | 下一阶段，当前数据不足 |

### 7.2 确定性分析工具

| ID | 需求 | 优先级 | 状态 |
|---|---|---:|---|
| FR-TOOL-001 | 查看数据总览和按语言分布 | P0 | 已实现 |
| FR-TOOL-002 | 按语言/domain 查询指标 | P0 | 已实现 |
| FR-TOOL-003 | 跨语言比较 | P0 | 已实现 |
| FR-TOOL-004 | 跨 domain 比较 | P0 | 已实现 |
| FR-TOOL-005 | 按错误数、错误率、准确率或总量排名 | P0 | 已实现 |
| FR-TOOL-006 | 按关键词、语言、domain 和结果搜索 Case | P0 | 已实现 |
| FR-TOOL-007 | 使用 stable case_id 获取详情 | P0 | 已实现 |
| FR-TOOL-008 | 比较 CSV 与 JSON 口径 | P0 | 已实现 |
| FR-TOOL-009 | 查询数据质量问题 | P0 | 已实现 |
| FR-TOOL-010 | 解释平台能力并拒答越界问题 | P0 | 已实现 |
| FR-TOOL-011 | 下载当前结果为 CSV/JSON | P1 | 已实现 |
| FR-TOOL-012 | 生成正式分析报告 | P1 | 下一阶段 |
| FR-TOOL-013 | NLU 指标、语言/Domain 排名、错误分布、明细检索和标签质量工具 | P1 | 已实现六个只读工具 |
| FR-TOOL-014 | ASR 与 NLU 跨 Provider 联合查询 | P1 | 已实现 Supervisor 同轮并行调用 |

### 7.3 Agent 编排

| ID | 需求 | 优先级 | 状态 |
|---|---|---:|---|
| FR-AGENT-001 | 使用统一状态保存问题、上下文、计划和 Observation | P0 | 已实现 |
| FR-AGENT-002 | Supervisor 将全局目标拆成 SpecialistTask | P0 | 已实现 |
| FR-AGENT-003 | 最多执行三轮，避免无限循环 | P0 | 已实现 |
| FR-AGENT-004 | 并行执行相互独立的 Analysis 与 Governance 任务和 ToolCall | P1 | 已实现 |
| FR-AGENT-005 | 提供统一的只读数据分析 Agent | P0 | 已实现 |
| FR-AGENT-006 | 提供具有治理状态的数据治理 Agent | P0 | 已实现 |
| FR-AGENT-007 | Planner 只能选择已注册工具 | P0 | 已实现 |
| FR-AGENT-008 | 使用 Pydantic 校验 MultiAgentPlan、SpecialistTask、SpecialistResult 和回答 | P0 | 已实现 |
| FR-AGENT-009 | 支持多轮语言/domain 上下文继承 | P1 | 已实现 |
| FR-AGENT-010 | 对越界问题进行可解释拒答 | P0 | 已实现 |
| FR-AGENT-011 | Analysis 与 Governance Agent 只能执行各自工具白名单 | P0 | 已实现 |
| FR-AGENT-012 | 根据 Observation 跨轮委派分析或治理任务 | P0 | 已实现 |
| FR-AGENT-013 | 支持离线 Supervisor 和 Azure Supervisor/业务 Agent 规划 | P0 | 已完成 GPT-5.4-mini 在线简单/复合链路、25 条完整确定性回归及 6 条含 Judge smoke 验收 |
| FR-AGENT-014 | 业务 Agent LLM 规划失败时退回 Supervisor 调用 | P1 | 已实现 |
| FR-AGENT-015 | AnswerSynthesizer、Grounding 和 Human Confirmation 保持为组件/人工节点 | P0 | 已实现 |
| FR-AGENT-016 | Azure Planner/Composer SDK 异常时使用确定性实现降级并记录 Trace | P1 | 已实现并通过故障注入测试 |

### 7.4 数据治理工作流

| ID | 需求 | 优先级 | 状态 |
|---|---|---:|---|
| FR-GOV-001 | 扫描契约与 Provider 特有规则并持久化 Issue | P0 | 已实现 |
| FR-GOV-002 | 查询 Issue 状态、严重级别、来源和证据 | P0 | 已实现 |
| FR-GOV-003 | 为 mutable 字段创建 Change Draft | P0 | 已实现 |
| FR-GOV-004 | 发布前生成 Diff Preview 与契约校验 | P0 | 已实现 |
| FR-GOV-005 | 用户检查 Diff 和契约结果后将 Draft 标记为 CONFIRMED | P0 | 已实现 |
| FR-GOV-006 | 确认必须由 UI/API 显式动作触发，聊天 Agent 无确认权限 | P0 | 已实现 |
| FR-GOV-007 | CONFIRMED 后发布累积 Patch Dataset Version | P0 | 已实现 |
| FR-GOV-008 | raw 源文件永不覆盖 | P0 | 已实现 |
| FR-GOV-009 | 发布后重建 Warehouse 并应用 active overlay | P0 | 已实现 |
| FR-GOV-010 | 支持回滚父版本 | P0 | 已实现 |
| FR-GOV-011 | 所有状态变化写入 Audit Log | P0 | 已实现 |
| FR-GOV-012 | 范围级口径问题禁止单字段自动修复 | P0 | 已实现 |

### 7.5 知识与 Skills

| ID | 需求 | 优先级 | 状态 |
|---|---|---:|---|
| FR-KNOW-001 | 从 Markdown 业务文档构建知识索引 | P1 | 已实现 |
| FR-KNOW-002 | 使用 FTS5 检索指标定义和数据口径 | P1 | 已实现 |
| FR-KNOW-003 | 支持中英文术语查询改写 | P1 | 已实现 |
| FR-KNOW-004 | 从 SKILL.md 动态加载分析 SOP | P1 | 已实现 |
| FR-KNOW-005 | 支持 Skill 运行时重载 | P1 | 已实现 |
| FR-KNOW-006 | FTS5/BM25 + ChromaDB + RRF + 轻量重排 Hybrid RAG | P0 | 已实现并通过 18/18 离线检索评测；Azure Embedding 为可选增强，未部署 |
| FR-KNOW-007 | Citation Span 精确到文档片段 | P2 | 下一阶段 |
| FR-KNOW-008 | Markdown 支持来源类型、验证日期、可信度、敏感级别和 `index: false` | P0 | 已实现 |
| FR-KNOW-009 | PDF/DOCX 原件不直接索引，只检索经过脱敏和来源标注的 Markdown | P0 | 已实现 |
| FR-KNOW-010 | 合成银标与人工确认核心集分开生成、执行和报告 | P0 | 已实现 |

### 7.6 可信回答与可观察性

| ID | 需求 | 优先级 | 状态 |
|---|---|---:|---|
| FR-TRUST-001 | 每个 Observation 携带来源和 warning | P0 | 已实现 |
| FR-TRUST-002 | 校验回答数字是否出现在问题或 Observation | P0 | 已实现 |
| FR-TRUST-003 | 校验引用路径真实存在 | P0 | 已实现 |
| FR-TRUST-004 | Azure 回答未通过 Grounding 时降级 | P0 | 已实现 |
| FR-TRUST-005 | 持久化 Plan、Tool、Replan、Verify、Answer Trace | P0 | 已实现 |
| FR-TRUST-006 | 持久化 LLM operation、延迟、token 和错误类型 | P1 | 已实现 |
| FR-TRUST-007 | 不在 LLM telemetry 中保存 Key 或 Prompt 正文 | P0 | 已实现 |
| FR-TRUST-008 | 复杂语义事实映射与 Citation Span 校验 | P2 | 下一阶段 |

### 7.7 接口与运维

| ID | 需求 | 优先级 | 状态 |
|---|---|---:|---|
| FR-OPS-001 | 提供 Streamlit 分析界面 | P0 | 已实现 |
| FR-OPS-002 | 提供 FastAPI 与 Swagger | P0 | 已实现 |
| FR-OPS-003 | 提供 CLI 问答、导入、评测和服务命令 | P0 | 已实现 |
| FR-OPS-004 | 提供 Azure init/status/test 向导 | P0 | 已实现 |
| FR-OPS-005 | 提供 Provider、Tool、Skills、LLM 监控 | P1 | 已实现 |
| FR-OPS-006 | 提供端到端评测和独立模式基线 | P0 | 已实现 |
| FR-OPS-007 | 核心指标回归超过 5% 时返回失败 | P1 | 已实现 |
| FR-OPS-008 | Docker Compose 配置 | P1 | 已实现，当前机器未运行验收 |
| FR-OPS-009 | 单机版仅记录本地操作标识和 Audit，不引入企业身份、RBAC 或电子签名 | P0 | 已实现 |
| FR-OPS-010 | GitHub Actions 执行 Ruff、Mypy、无数据测试、依赖和镜像构建门禁 | P1 | 工作流已实现；推送 GitHub 后首次远程验收 |
| FR-OPS-011 | CD、并发压力测试和生产部署 | P2 | 下一阶段 |

---

## 8. 业务规则

| ID | 规则 |
|---|---|
| BR-001 | Case 查询和默认指标使用 CSV 行口径 |
| BR-002 | 原始 `*_output.json` 是独立运行汇总口径 |
| BR-003 | CSV 和 JSON 不一致时只报告差异，不静默修复或合并 |
| BR-004 | 准确率由 `correct / total * 100` 计算 |
| BR-005 | 错误率由 `errors / total * 100` 计算 |
| BR-006 | 所有项目事实和数字必须来自注册工具或知识来源 |
| BR-007 | LLM 不直接计算业务指标，不执行任意 SQL |
| BR-008 | “最好/最差”必须说明采用的指标 |
| BR-009 | 无证据时不得推测错误根因 |
| BR-010 | 越界请求返回能力范围，不生成通用常识答案 |
| BR-011 | API Key 只允许存在于环境变量或本机 `.env` |
| BR-012 | 更新评测基线前必须人工确认当前结果正确 |
| BR-013 | 治理变更确认前必须展示 operation、entity key、field、before、after 和 Data Contract 检查结果 |
| BR-014 | 回答中的语言、领域、Case ID、治理 Issue ID 和来源范围必须由 Observation 或注册来源支持 |

---

## 9. 非功能需求

### 9.1 正确性

| ID | 要求 |
|---|---|
| NFR-COR-001 | 确定性指标与 DuckDB 查询结果一致 |
| NFR-COR-002 | 回答数字必须通过 Grounding |
| NFR-COR-003 | 来源路径必须真实存在 |
| NFR-COR-004 | 当前 25 条核心回归集必须全部通过 |
| NFR-COR-005 | 专业 Agent 路由通过 Agent Accuracy 验证 |
| NFR-COR-006 | 任务理解通过 Intent Accuracy 与 Entity Accuracy 独立验证 |
| NFR-COR-007 | Hybrid RAG 必须通过 Recall@3 >= 0.90、MRR >= 0.75 的门槛 |

### 9.2 可靠性

| ID | 要求 |
|---|---|
| NFR-REL-001 | 工具默认超时 10 秒 |
| NFR-REL-002 | 工具连续失败三次后熔断 30 秒 |
| NFR-REL-003 | 相同工具参数支持 TTL 缓存 |
| NFR-REL-004 | Azure 配置缺失时离线模式可继续运行 |
| NFR-REL-005 | Azure 回答 Grounding 失败时自动降级 |

### 9.3 性能

| ID | 要求 |
|---|---|
| NFR-PERF-001 | 无源文件变化时复用 Warehouse |
| NFR-PERF-002 | 相互独立的 ToolCall 并行执行 |
| NFR-PERF-003 | 离线核心评测平均执行时间应维持在基线可接受范围 |
| NFR-PERF-004 | UI 不一次性渲染无限量 Case，工具限制最大返回条数 |

### 9.4 安全

| ID | 要求 |
|---|---|
| NFR-SEC-001 | 不输出、记录或提交 API Key |
| NFR-SEC-002 | SQL 使用受控模板和参数绑定 |
| NFR-SEC-003 | 原始数据目录只读，不由平台修改 |
| NFR-SEC-004 | 评测数据文件路径必须限制在指定目录内 |
| NFR-SEC-005 | 未经公司批准不得将内部数据发送到外部模型服务 |
| NFR-SEC-006 | raw 数据不可覆盖，发布只使用版本化 overlay |
| NFR-SEC-007 | 发布前必须有用户显式 Diff 确认记录 |
| NFR-SEC-008 | 聊天 Agent 无确认、发布或回滚权限 |

### 9.5 可维护性与扩展性

| ID | 要求 |
|---|---|
| NFR-MNT-001 | UI、API 和 CLI 共用 AgentService |
| NFR-MNT-002 | 新数据源通过 DataProvider 接口接入 |
| NFR-MNT-003 | Planner/Composer 可离线与 Azure 替换 |
| NFR-MNT-004 | 数据契约统一由 Pydantic 定义 |
| NFR-MNT-005 | 业务 SOP 通过 Skills 配置而非散落在代码中 |

### 9.6 可用性与无障碍

| ID | 要求 |
|---|---|
| NFR-UX-001 | 首屏提供推荐分析问题 |
| NFR-UX-002 | 回答必须展示 Grounding 状态、工具数和 Trace ID |
| NFR-UX-003 | Observation 和来源默认折叠，可按需展开 |
| NFR-UX-004 | 390px 移动视口不得出现页面级水平溢出 |
| NFR-UX-005 | 不只依赖颜色表达成功、警告和失败 |

---

## 10. 原始数据与假设

### 10.1 当前已知数据质量问题

1. French `generalControl` CSV 有 5 列，只使用前 4 列；
2. Portuguese `carControl` CSV 有 12 列，只使用前 4 列；
3. French `carControl` CSV 为 4,147 行，JSON 声明 4,178 条。

这些问题必须显示为 warning，不由平台自动修改。

### 10.2 当前假设

- 七种语言的核心 CSV 前四列语义一致；
- CSV 中 `✓` 代表正确，其他当前已知结果标记为错误；
- 当前目录结构由配置显式映射；
- 单机 Streamlit/SQLite 足以支撑 MVP；
- 当前用户具有访问全部源文件的本机权限。

---

## 11. 不在当前范围

- 智能客服订单、退款、工单和转人工；
- TN 规则生成、错误自动分诊和生产规则发布；
- ASR 模型训练或修复；
- 任意自然语言转 SQL 并直接执行；
- 未经审批的外部联网搜索；
- 用户画像和跨组织数据共享；
- 未经人工验证的业务根因结论；
- 多 Agent 无约束自由协商（当前采用可控的 Supervisor-led 协作）；
- Experiment Agent（缺少第二个真实数据/模型版本）；
- Reporting Agent（缺少正式签审和发布需求）；
- 声称已经实现标准 MCP Server。

---

## 12. 验收标准

### 12.1 功能验收

- [x] `data-agent ingest` 导入 92,301 条 Case 和 42 条 JSON 汇总；
- [x] 整体指标、语言排名、domain 比较、Case 搜索正常；
- [x] 复合问题产生至少两轮规划和并行工具；
- [x] 多轮问题正确继承语言上下文；
- [x] CSV/JSON 差异被显式提示；
- [x] 越界问题不生成无来源答案；
- [x] Trace 可展示完整执行事件；
- [x] Azure 缺少配置时显示可操作指引；
- [x] Azure 配置后可执行最小连接测试；
- [x] 25 条 Multi-Agent 回归评测全部通过；
- [x] 62 条合成银标回归评测全部通过，并明确不等同人工金标；
- [x] Intent Accuracy 和 Entity Accuracy 为 100%（当前标注集）；
- [x] Agent Accuracy 为 100%（当前回归集）；
- [x] 18 条 Retrieval Eval 全部通过，且 Recall@3/MRR 达到门槛；
- [x] 非 ASR 销售 Contract Scanner 测试通过；
- [x] Change Draft → Confirm → Publish → Rollback 测试通过；
- [x] 变更预览包含结构化 before/after Diff 与 Data Contract 检查；
- [x] 发布与回滚前后 raw 文件内容保持不变；
- [x] 相对基线无超过 5% 的回归。

### 12.2 工程验收

- [x] `pytest` 全部通过；
- [x] VS Code 使用项目 `.venv` 且本次涉及源码无诊断；
- [x] LLM telemetry 不保存 API Key、Prompt 或回答正文；
- [x] Swagger 可查看核心接口；
- [x] 390px 和桌面宽度下主要工作流可用；
- [x] README、需求、原型和架构设计互相一致。

---

## 13. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| Azure 调用产生费用或暂时不可用 | 在线回归中断或成本失控 | 默认 6 条 smoke、全量回归显式确认、离线 Planner 降级、per-run 成本与 P95 |
| 业务文档过少 | RAG 价值有限 | 优先补充政策、SOP、domain 定义和已知问题 |
| 数据口径冲突 | 指标误读 | 分离 CSV/JSON scope；回答强制携带 warning |
| 自然语言表达超出离线路由 | 工具选择失败 | Azure Planner；扩大回归集；可解释拒答 |
| 模型编造数字 | 决策风险 | 工具计算、Grounding、引用和降级 |
| 单机架构扩展受限 | 并发和权限不足 | 下一阶段拆服务、接企业身份和持久化服务 |
| 平台名称过度承诺 | 汇报可信度下降 | 明确当前验证 ASR 与 NLU 两个 Provider，不称零配置通用平台或企业级生产系统 |

---

## 14. 需求追踪矩阵摘要

| 需求组 | 原型页面 | 架构组件 |
|---|---|---|
| FR-DATA | UI-03 系统与评测 | ARC-07 Warehouse / ARC-08 Provider |
| FR-TOOL | UI-01 分析工作台 | ARC-06 Tool Runtime / ARC-08 Provider |
| FR-AGENT | UI-01、UI-02 | ARC-03 LangGraph / ARC-04 Planner |
| FR-KNOW | UI-01、UI-03 | ARC-09 知识检索 Provider / ARC-11 Skills |
| FR-TRUST | UI-01、UI-02 | ARC-05 Composer / ARC-10 Grounding / ARC-12 State Store |
| FR-OPS | UI-03 | ARC-01 Interface / ARC-02 AgentService / ARC-13 Evaluation |

完整页面定义见 `PROTOTYPE_DESIGN.md`，完整组件定义见 `ARCHITECTURE_DESIGN.md`。
