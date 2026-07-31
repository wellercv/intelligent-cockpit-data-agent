# 业务素材接入指南

## 原始结构化数据

现有七语种 ASR 目录和 `离线NLU重测报告_20260710.xlsx` 保持在 `data_insight_agent` 同级，不修改。`config/sources.yaml` 分别注册 ASR CSV/JSON 与 NLU Excel；新增不同数据形态时实现新的 Provider。

NLU Excel Provider 只使用报告中实际存在的三类事实：完整汇总指标、标签问题明细和模型错误明细。报告不包含所有正确样本的逐条记录，因此不能用它搜索全部 104,897 条原始 NLU Case。Expected/Prediction JSON 会结构化解析，解析失败保留为可治理事实，Excel 原件始终只读。

## 业务知识文档

系统只索引 `knowledge/**/*.md`。PDF/DOCX 原件可以放在目录中留存，但不会直接进入 RAG；应先去除 IP、账号、人员姓名、内部路径和未经核验的说法，再生成 Markdown 派生文档。

放入 `knowledge/`：

```text
knowledge/
├─ policies/          评测政策和数据口径
├─ domains/           domain、intent、slot 定义
├─ processes/         测试和 Review SOP
├─ glossary/          术语与字段说明
├─ known_issues/      已知问题和 Bug 说明
└─ report_templates/  周报和汇报模板
```

建议优先补充：

1. 指标和数据来源优先级；
2. 七语种 domain 定义；
3. 测试执行与 Review SOP；
4. 已知问题；
5. 历史分析报告；
6. 标准报告模板。

每份资料建议写明：标题、版本、生效日期、负责人、适用语言/domain 和敏感等级。

当前支持 YAML front matter：

```yaml
---
title: ASR 质量评测指标与测试集实践
source_type: public_official
public_sources:
	- https://example.com/official-source
verified_on: 2026-07-28
confidence: high
confidentiality: public
---
```

设置 `index: false` 可让 Markdown 留在目录中但不进入检索。内部附件生成的摘要应使用 `source_type: internal_sanitized`，网络资料应优先引用官方来源并保留验证日期。

## Data Contract 与治理规则

新数据集先在 `config/contracts/` 定义通用契约：

```text
required / type / enum / range / unique / mutable
```

跨业务通用检查由 Contract Scanner 执行；只有确实依赖领域语义的规则才放入 Governance Adapter。接入时还应明确：

1. 稳定实体主键；
2. 字段类型、允许值和范围；
3. 哪些字段允许 Patch；
4. 数据 Owner、变更原因和本地确认记录；
5. 发布后验证与回滚条件。

raw 数据保持只读。治理修正写入版本化 Patch overlay，不直接改源文件。

## Agent Skills

“事实是什么”放 `knowledge/`；“Agent 应该怎样工作”放 `skills/`。

```text
skills/<skill-name>/SKILL.md
```

Skills 适合保存：分析步骤、必须检查的口径、禁止推测事项、输出格式和升级条件。

## Agent 评测集

标准问题和期望行为放入：

```text
eval/datasets/*.json
```

应覆盖指标、比较、搜索、多轮、拒答、口径冲突、数据缺失、治理路由、状态查询和复杂组合问题。

当前评测分两组：

- `core_questions.json`：25 条人工确认核心用例；
- `synthetic_understanding.json`：由 `eval/generate_synthetic_dataset.py` 确定性生成的 62 条银标用例。
- `nlu_questions.json`：7 条 NLU 与 ASR+NLU 跨 Provider 回归用例。

银标用于模板覆盖和回归，不得写成“人工标注集”或“生产准确率”。新增真实日志后只需抽样复核高频失败问题，不要求一次性大规模人工标注。

## 敏感信息

- API Key、Token 和密码只放环境变量；
- IP、SSH 配置、个人信息、电话号码和账号进入知识库前应脱敏；
- 原始日志默认只读；
- 生成的 Warehouse、索引和会话数据库放 `data/`，不提交到 Git。
