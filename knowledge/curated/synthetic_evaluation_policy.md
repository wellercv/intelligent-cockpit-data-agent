---
title: Agent 合成银标评测集的用途与边界
source_type: project_policy
verified_on: 2026-07-28
confidence: high
confidentiality: internal
---

# Agent 合成银标评测集的用途与边界

## 定位

合成银标集由注册工具、语言、Domain、指标和治理规则的模板确定性生成，不声称是人工金标。它适合持续集成和低成本回归测试。

## 可以验证

- `metric_analysis`、`case_investigation`、`knowledge_qa`、`data_governance`、`mixed`、`out_of_scope` 等意图；
- 语言、Domain、指标、Case 编号和来源范围实体；
- Tool 选择和专业 Agent 路由；
- 已知模板下的 Grounding、引用和结果格式；
- Prompt 或 Planner 修改是否破坏既有能力。

## 不能替代

- 真实用户的模糊表达、方言、错别字和上下文省略；
- 未见过的业务问题与需求变化；
- ASR 音频和人工参考转写的声学质量评测；
- 生产用户满意度；
- 对开放式答案的完整人工审查。

## 使用规则

1. 合成集与核心手工确认集分开报告，不合并成“人工标注准确率”。
2. 生成器使用固定随机种子或确定性模板，保证可复现。
3. 每条记录保存模板 ID、数据来源和预期意图/实体。
4. 合成集 100% 只说明模板覆盖通过，不能外推到生产准确率。
5. 有真实使用日志后，可以只抽样复核高频失败问题，不要求一次性大规模人工标注。