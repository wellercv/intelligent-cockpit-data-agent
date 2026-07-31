"""Grounded deterministic and optional Azure answer composition."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import List, Sequence

from data_insight.llm import AzureLLMGateway
from data_insight.schemas import ConversationContext, ToolObservation
from data_insight.skills import SkillManager


def _pct(value: object) -> str:
    return f"{float(value or 0):.2f}%"


def _table(rows: Sequence[dict], columns: Sequence[tuple[str, str]], limit: int = 20) -> List[str]:
    lines = ["| " + " | ".join(label for _, label in columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows[:limit]:
        values = []
        for key, _ in columns:
            value = row.get(key, "")
            if isinstance(value, float):
                value = f"{value:.2f}"
            values.append(str(value).replace("|", "\\|"))
        lines.append("| " + " | ".join(values) + " |")
    return lines


class AnswerComposer(ABC):
    @abstractmethod
    def compose(self, question: str, context: ConversationContext, observations: Sequence[ToolObservation]) -> str:
        pass


class OfflineComposer(AnswerComposer):
    def compose(self, question: str, context: ConversationContext, observations: Sequence[ToolObservation]) -> str:
        successful = [item for item in observations if item.success]
        if not successful:
            errors = [item.error for item in observations if item.error]
            return "## 无法完成\n\n没有获得可用的数据证据。" + ("\n\n" + "；".join(errors) if errors else "")
        lines = ["## 结论"]
        first = successful[0]
        lines.extend(self._conclusion(first))
        if len(successful) > 1:
            lines.extend(["", "### 协作摘要"])
            for observation in successful[1:]:
                lines.extend(self._conclusion(observation))
        lines.extend(["", "## 数据证据"])
        for observation in successful:
            lines.extend(self._observation_block(observation))
        warnings = list(dict.fromkeys(warning for item in observations for warning in item.warnings))
        if warnings:
            lines.extend(["", "## 口径与限制"])
            lines.extend(f"- {warning}" for warning in warnings)
        return "\n".join(lines)

    def _conclusion(self, observation: ToolObservation) -> List[str]:
        data, rows, tool = observation.data, observation.rows, observation.tool_name
        if tool == "platform_capabilities":
            return [
                "这个问题超出了当前已注册的数据范围，因此我无法查询或确认你请求的实时外部信息。"
                "平台只回答七语种 ASR 指标、比较、Case、数据口径、质量和知识定义问题；"
                "没有生成无来源答案。请使用组织批准的对应业务系统或权威服务查询当前信息。"
            ]
        if tool == "dataset_overview":
            return [f"当前已接入 **{data['language_count']} 种语言**、**{data['domain_count']} 个 domain**，CSV Case 共 **{data['total']:,}** 条，其中错误 **{data['errors']:,}** 条、结果未知 **{data.get('unknown_count', 0):,}** 条，整体准确率 **{_pct(data['accuracy_pct'])}**。"]
        if tool == "nlu_report_overview":
            return [
                f"NLU 离线重测报告覆盖 **{data['sample_count']:,}** 条样本；"
                f"修正标签口径后的 Exact Match Accuracy 为 "
                f"**{_pct(data['corrected_accuracy_pct'])}**，"
                f"模型错误 **{data['model_errors']:,}** 条。"
            ]
        if tool == "nlu_compare_accuracy" and rows:
            dimension = data["dimension"]
            top = rows[0]
            return [
                f"按修正标签口径后的 NLU Exact Match Accuracy "
                f"{'升序' if data.get('order') == 'worst' else '降序'}排列，"
                f"首位是 **{top[dimension]}**（"
                f"{_pct(top['corrected_accuracy_pct'])}）。"
            ]
        if tool == "nlu_error_breakdown" and rows:
            group_by = data["group_by"]
            top = rows[0]
            return [
                f"在模型错误明细子集中，按 **{group_by}** 聚合后数量最多的是 "
                f"**{top[group_by]}**，共 **{top['count']:,}** 条（"
                f"{_pct(top['share_pct'])}）。"
            ]
        if tool == "search_nlu_errors":
            return [
                f"NLU 模型错误明细共命中 **{data.get('total_matches', 0):,}** 条，"
                f"当前返回 **{data.get('returned', len(rows))}** 条。"
            ]
        if tool == "get_nlu_error_detail":
            return [
                f"已定位 NLU 错误 `{data.get('error_id')}`："
                f"错误类型 **{data.get('error_type')}**，期望 Domain/Intent 为 "
                f"**{data.get('expected_domain')}/{data.get('expected_intent')}**。"
            ]
        if tool == "nlu_label_quality":
            return [
                f"报告记录 **{data.get('numeric_label_issues', 0):,}** 条数值槽位"
                f"标注问题，并有 **{data.get('language_naming_affected', 0):,}** 条样本"
                f"受语言命名口径影响；Excel 原件保持只读。"
            ]
        if tool == "get_metrics":
            scope = " / ".join(item for item in (data.get("language"), data.get("domain")) if item) or "全部数据"
            unknown_text = (
                f"、结果未知 **{data.get('unknown_count', 0):,}** 条"
                if data.get("unknown_count")
                else ""
            )
            return [f"{scope} 共 **{data.get('total', 0):,}** 条，错误 **{data.get('errors', 0):,}** 条{unknown_text}，准确率 **{_pct(data.get('accuracy_pct'))}**。"]
        if tool in {"compare_languages", "compare_domains", "rank_dimensions"} and rows:
            dimension = "language" if "language" in rows[0] else "domain"
            top = rows[0]
            metric = data.get("metric", "error_rate")
            value_key = {"errors": "errors", "accuracy": "accuracy_pct", "total": "total"}.get(metric, "error_rate_pct")
            return [f"按 **{metric}** 排序，当前首位是 **{top[dimension]}**（{top[value_key]}）。下表给出完整可比结果。"]
        if tool == "search_cases":
            return [f"共命中 **{data.get('total_matches', 0)}** 条案例，当前返回 **{data.get('returned', len(rows))}** 条。"]
        if tool == "get_case_detail":
            return [f"共定位到 **{data.get('match_count', len(rows))}** 条匹配 Case。"]
        if tool == "compare_source_scopes":
            return ["CSV Case 行与原始 JSON 汇总属于不同数据口径，差异已逐项列出，平台不会自动合并。"]
        if tool == "data_quality":
            return [f"当前查询范围发现 **{data.get('count', len(rows))}** 个数据质量问题。"]
        if tool == "search_knowledge":
            return [f"从业务知识库检索到 **{data.get('returned', len(rows))}** 个相关片段。"]
        if tool == "governance_scan":
            return [
                f"数据契约扫描发现并跟踪 **{data.get('finding_count', len(rows))}** 个治理候选；原始数据没有被修改。"
            ]
        if tool == "list_governance_issues":
            return [f"当前查询范围共有 **{data.get('count', len(rows))}** 个治理 Issue。"]
        if tool == "get_governance_issue":
            return [
                f"治理 Issue `{data.get('issue_id')}` 当前状态为 **{data.get('status')}**。"
            ]
        if tool == "list_change_requests":
            return [f"当前查询范围共有 **{data.get('count', len(rows))}** 个变更申请。"]
        if tool == "preview_change":
            return [
                f"变更 `{data.get('change_id')}` 仅完成预览，契约校验结果为 **{'通过' if data.get('valid') else '不通过'}**；尚未发布。"
            ]
        return ["已根据工具返回的事实完成查询。"]

    def _observation_block(self, observation: ToolObservation) -> List[str]:
        tool, data, rows = observation.tool_name, observation.data, observation.rows
        lines = [f"### `{tool}`"]
        if tool == "platform_capabilities":
            lines.append("**支持的任务：**")
            lines.extend(f"- {item}" for item in data.get("supported", []))
            lines.append("**可以这样问：**")
            lines.extend(f"- {item}" for item in data.get("examples", []))
        elif tool == "dataset_overview":
            lines.extend(_table(data.get("by_language", []), [("language", "语言"), ("total", "总数"), ("correct", "正确"), ("errors", "错误"), ("unknown_count", "未知"), ("accuracy_pct", "准确率 %")]))
        elif tool == "nlu_report_overview":
            lines.extend(
                _table(
                    data.get("by_language", []),
                    [
                        ("language", "语言"),
                        ("total", "样本数"),
                        ("model_errors", "模型错误"),
                        ("raw_accuracy_pct", "原始标注准确率 %"),
                        ("corrected_accuracy_pct", "修正后准确率 %"),
                        ("improvement_pct", "口径修正提升 %"),
                    ],
                )
            )
        elif tool == "nlu_compare_accuracy":
            dimension = data["dimension"]
            lines.extend(
                _table(
                    rows,
                    [
                        (dimension, "语言" if dimension == "language" else "Domain"),
                        ("total", "样本数"),
                        ("corrected_correct", "修正后正确"),
                        ("model_errors", "模型错误"),
                        ("raw_accuracy_pct", "原始准确率 %"),
                        ("corrected_accuracy_pct", "修正后准确率 %"),
                    ],
                )
            )
        elif tool == "nlu_error_breakdown":
            group_by = data["group_by"]
            lines.extend(
                _table(
                    rows,
                    [(group_by, group_by), ("count", "错误数"), ("share_pct", "占比 %")],
                    limit=50,
                )
            )
        elif tool == "search_nlu_errors":
            for row in rows[:20]:
                lines.extend(
                    [
                        f"- **{row['language']} · {row.get('expected_domain')} · "
                        f"{row.get('expected_intent')}** `{row['error_id']}`",
                        f"  - Query：{row['query_text']}",
                        f"  - 类型：{row['error_type']}；预测 Intent："
                        f"{row.get('predicted_intent') or '无法解析'}",
                    ]
                )
        elif tool == "get_nlu_error_detail":
            lines.extend(
                [
                    f"- **Query**：{data.get('query_text')}",
                    f"- **Expected**：`{data.get('expected_json')}`",
                    f"- **Predicted**：`{data.get('predicted_json')}`",
                    f"- **预测 JSON 可解析**：{data.get('predicted_parse_ok')}",
                    f"- **来源行**：{data.get('source_sheet')} / {data.get('source_row')}",
                ]
            )
        elif tool == "nlu_label_quality":
            lines.extend(
                _table(
                    rows,
                    [
                        ("issue_kind", "问题类型"),
                        ("source_file", "文件"),
                        ("language", "语言"),
                        ("domain", "Domain"),
                        ("changed_slot", "字段/槽位"),
                        ("affected_count", "影响数"),
                    ],
                    limit=50,
                )
            )
        elif tool in {"compare_languages", "compare_domains", "rank_dimensions"}:
            dimension = "language" if rows and "language" in rows[0] else "domain"
            lines.extend(_table(rows, [(dimension, "语言" if dimension == "language" else "Domain"), ("total", "总数"), ("errors", "错误"), ("unknown_count", "未知"), ("accuracy_pct", "准确率 %"), ("error_rate_pct", "错误率 %")]))
        elif tool == "get_metrics":
            lines.append(f"- 数据口径：`{data.get('source_scope')}`")
            unknown_text = (
                f"；未知：**{data['unknown_count']:,}**"
                if "unknown_count" in data
                else ""
            )
            lines.append(f"- 总数：**{data.get('total', 0):,}**；正确：**{data.get('correct', 0):,}**；错误：**{data.get('errors', 0):,}**{unknown_text}；准确率：**{_pct(data.get('accuracy_pct'))}**")
        elif tool in {"search_cases", "get_case_detail"}:
            for row in rows[:20]:
                lines.extend([f"- **{row['language']} · {row['domain']} · {row['case_no']}** `{row['case_id']}`", f"  - REF：{row['reference_text']}", f"  - HYP：{row['hypothesis_text']}"])
        elif tool == "compare_source_scopes":
            lines.extend(_table(rows, [("language", "语言"), ("domain", "Domain"), ("csv_total", "CSV 总数"), ("json_total", "JSON 总数"), ("total_delta", "总数差"), ("csv_errors", "CSV 错误"), ("json_errors", "JSON 错误"), ("error_delta", "错误差")]))
        elif tool == "data_quality":
            lines.extend(_table(rows, [("severity", "级别"), ("language", "语言"), ("domain", "Domain"), ("issue_code", "问题"), ("detail", "说明")]))
        elif tool == "search_knowledge":
            for row in rows:
                lines.extend([f"- **{row['title']}**", row["content"]])
        elif tool in {"governance_scan", "list_governance_issues"}:
            lines.extend(
                _table(
                    rows,
                    [
                        ("issue_id", "Issue"),
                        ("severity", "级别"),
                        ("rule_id", "规则"),
                        ("entity_key", "实体"),
                        ("status", "状态"),
                        ("detail", "说明"),
                    ],
                    limit=50,
                )
            )
        elif tool == "get_governance_issue":
            lines.extend(f"- **{key}**：`{value}`" for key, value in data.items())
        elif tool == "list_change_requests":
            lines.extend(
                _table(
                    rows,
                    [
                        ("change_id", "Change"),
                        ("issue_id", "Issue"),
                        ("field_name", "字段"),
                        ("status", "状态"),
                        ("requested_by", "创建人"),
                    ],
                    limit=50,
                )
            )
        elif tool == "preview_change":
            lines.extend(f"- **{key}**：`{value}`" for key, value in data.items())
        return lines


class AzureComposer(AnswerComposer):
    def __init__(self, skills: SkillManager, gateway: AzureLLMGateway) -> None:
        self.skills = skills
        self.gateway = gateway

    def compose(self, question: str, context: ConversationContext, observations: Sequence[ToolObservation]) -> str:
        payload = {"question": question, "context": context.model_dump(mode="json"), "observations": [item.model_dump(mode="json") for item in observations]}
        system = (
            "Answer in Chinese using only supplied tool observations. Never calculate or invent business numbers. "
            "Distinguish facts from analysis, preserve source-scope warnings, and say when evidence is insufficient. "
            "When observations contain canonical language, domain, case, issue, or metric labels, include each exact "
            "canonical label at least once, optionally followed by a Chinese explanation. "
            "Use sections: 结论, 数据证据, 分析, 口径与限制. Do not create a separate source list.\n\n"
            + self.skills.prompt_for(question, "answer")
        )
        return self.gateway.chat(
            "answer_composition",
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0,
        )
