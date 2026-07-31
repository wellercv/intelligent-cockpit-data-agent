---
title: ASR 质量评测指标与测试集实践
source_type: public_official
public_sources:
  - https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-custom-speech-evaluate-data
  - https://github.com/usnistgov/SCTK
verified_on: 2026-07-28
confidence: high
confidentiality: public
---

# ASR 质量评测指标与测试集实践

## WER

词错误率（Word Error Rate）按参考转写中的词数 $N$ 归一化：

`WER = (I + D + S) / N * 100%`

- `I`：识别结果中多出的插入词；
- `D`：参考转写中存在但识别结果漏掉的删除词；
- `S`：参考词被识别成其他词的替换词。

WER 越低通常越好。阈值必须结合语言、场景、音频条件和产品目标设定，不能把某个公开示例阈值直接当作本项目验收线。NIST SCTK 的 `sclite` 可用于本地对齐和评分。

## TER 与显示格式

Token Error Rate 使用类似公式，但在 Token 层面计算，可把标点、大小写和逆文本归一化等显示格式差异纳入分析。涉及导航地址、数字、日期和媒体实体时，应同时检查词汇错误与最终显示格式。

## 当前项目的 Case Accuracy

本项目 CSV 中的 `accuracy = correct / total * 100%` 是 Case 级通过率，不等同于 WER 或 TER。一个 Case 只要被标记为错误，就计入错误 Case；它不会表达一句话内部有多少个插入、删除或替换。

因此回答必须明确指标名称：

- Case Accuracy / Error Rate：适合测试集通过率与语言、Domain 排名；
- WER / TER：适合转写文本的细粒度识别质量；
- NLU Intent/Slot 指标：适合语义理解，不应与 ASR 指标混用。

## 测试集要求

公开评测指南强调使用音频及对应参考转写，并让测试音频具有代表性。用于评估的声学数据应与训练数据分离，才能减少数据泄漏并更真实地估计模型表现。

车载语音测试集至少应分层记录：语言与 locale、Domain、车辆或设备环境、近讲/远讲、噪声条件、说话风格、实体类型、模型版本和数据版本。对比两个模型时应使用相同测试集和相同文本规范化规则。

## 错误分析

总体指标只能回答“表现如何”，不能单独回答“为什么”。定位问题时应进一步比较插入、删除、替换的分布，并检查音频质量、背景噪声、领域实体覆盖、文本规范化和集成链路。