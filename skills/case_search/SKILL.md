---
name: Case investigation
agents: analysis_agent,answer
keywords: case,REF,HYP,案例,样本,查找,搜索,包含
priority: 90
enabled: true
---

# Case investigation

- Preserve the language and domain filters from the user request.
- Case identifiers can repeat across languages; prefer the stable case_id.
- Report returned count separately from total matches.
- Never describe a case as a root cause unless a source explicitly provides that label.
