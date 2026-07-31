---
title: Knowledge base maintenance guide
index: false
---

# 知识库维护说明

系统只索引 `knowledge/**/*.md`，不会直接索引 PDF、DOCX 或其他二进制附件。

原始附件可能包含内部地址、账号、人员姓名、商业安排或未经核验的外部链接，因此必须先完成以下处理，再生成可检索 Markdown：

1. 删除密钥、IP、账号、个人姓名和内部机器路径；
2. 区分官方公开来源、内部脱敏材料、项目规则和合成内容；
3. 写明验证日期、可信度和适用边界；
4. 对可能变化的网页、榜单和政策保留来源 URL；
5. 不把“网页公开可访问”解释为“允许批量抓取或再分发”；
6. 不把派生摘要当作合同、法律意见或生产操作凭据。

推荐 front matter：

```yaml
---
title: 文档标题
source_type: public_official | internal_sanitized | project_policy | synthetic
verified_on: YYYY-MM-DD
confidence: high | medium | low
confidentiality: public | internal
---
```

需要保留但不允许进入检索的 Markdown，应设置 `index: false`。