---
name: Knowledge grounding
agents: analysis_agent,answer
keywords: 定义,含义,为什么,怎么计算,口径,definition,explain,policy
priority: 95
enabled: true
---

# Knowledge grounding

- Retrieve project definitions and source-scope policy from registered documents.
- Do not substitute general model memory for project-specific policy.
- Preserve document sources and state when the knowledge base has no matching evidence.
