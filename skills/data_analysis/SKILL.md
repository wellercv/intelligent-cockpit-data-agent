---
name: Multilingual data analysis
agents: orchestrator,analysis_agent,answer
keywords: compare,rank,highest,lowest,比较,排名,最高,最低,准确率,错误率
priority: 100
enabled: true
---

# Multilingual data analysis

- All metrics must come from deterministic tools, never from model arithmetic.
- State the source scope: CSV case rows or JSON run summary.
- For ambiguous words such as "best" or "worst", report the metric used.
- Distinguish observed facts from hypotheses and do not infer root causes without evidence.
