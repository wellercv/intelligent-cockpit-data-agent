---
name: Governed data change workflow
agents: orchestrator,data_governance_agent
keywords: 数据质量,治理,修复,变更,审批,发布,回滚,quality,governance,change,approval
priority: 110
enabled: true
---

# Governed data change workflow

- Raw source files are immutable and must never be overwritten.
- Scan findings are candidates; ambiguous business meaning requires human review.
- The Agent may create an Issue or Draft, but cannot approve its own request.
- Every published change requires a Diff Preview, a separate reviewer, a version ID, audit history, post-publish validation, and rollback support.
- Scope-level mismatches cannot be patched as a single field; request a corrected source file or an authoritative-source decision.