"""Deterministic and Azure-backed planning for the agent loop."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Sequence

from data_insight.llm import AzureLLMGateway
from data_insight.schemas import (
    AgentPlan,
    ConversationContext,
    PlanningDecision,
    TaskUnderstanding,
    ToolCall,
    ToolObservation,
)
from data_insight.skills import SkillManager

_LANGUAGE_ALIASES = {
    "Arabic": ["arabic", "阿拉伯语"],
    "English": ["english", "英语", "英文"],
    "French": ["french", "法语"],
    "German": ["german", "德语"],
    "Italian": ["italian", "意大利语"],
    "Portuguese": ["portuguese", "葡萄牙语"],
    "Spanish": ["spanish", "西班牙语"],
}
_DOMAIN_ALIASES = {
    "carControl": ["carcontrol", "car control", "车辆控制"],
    "generalControl": ["generalcontrol", "general control", "通用控制"],
    "mediaControl": ["mediacontrol", "media control", "媒体控制"],
    "naviControl": ["navicontrol", "navi control", "导航控制"],
    "phone": ["phone domain", "phone control", "电话领域", "电话控制"],
    "systemControl": ["systemcontrol", "system control", "系统控制"],
}
_CASE_RE = re.compile(r"(?:ASR\s*)?#?\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)
_STABLE_CASE_RE = re.compile(r"\b(?:arabic|english|french|german|italian|portuguese|spanish)-[a-z]+-[0-9a-f]{12}\b", re.IGNORECASE)
_ISSUE_RE = re.compile(r"\bDQ-[0-9a-f]{16}\b", re.IGNORECASE)
_NLU_ERROR_RE = re.compile(r"\bNLU-ERR-[0-9a-f]{12}\b", re.IGNORECASE)


class Planner(ABC):
    @abstractmethod
    def next_step(
        self,
        question: str,
        context: ConversationContext,
        observations: Sequence[ToolObservation],
        round_number: int,
    ) -> PlanningDecision:
        pass


class OfflinePlanner(Planner):
    """Safe planner for core analytical questions without an LLM dependency."""

    def __init__(self, tools: Sequence[Dict[str, Any]]) -> None:
        self.tool_names = {tool["name"] for tool in tools}

    def next_step(
        self,
        question: str,
        context: ConversationContext,
        observations: Sequence[ToolObservation],
        round_number: int,
    ) -> PlanningDecision:
        text = question.strip()
        lower = text.casefold()
        understanding = self.understand(text, context)
        completed = [item.tool_name for item in observations if item.success]
        if round_number >= 3:
            return PlanningDecision.answer(
                "Maximum planning rounds reached.",
                understanding,
            )
        languages = self.extract_languages(text)
        domains = self.extract_domains(text)
        inherited_languages = context.selected_languages if self._uses_context(lower) and not languages else []
        inherited_domains = context.selected_domains if self._uses_context(lower) and not domains else []
        languages = languages or inherited_languages
        domains = domains or inherited_domains

        if not completed:
            call = self._initial_call(text, lower, languages, domains, context)
            return PlanningDecision.execute(
                AgentPlan(
                    goal=question,
                    calls=[call],
                    rationale="Deterministic intent-to-tool routing",
                ),
                understanding=understanding,
            )

        wants_cases = any(token in lower for token in ("案例", "样本", "cases", "examples", "列出", "找出"))
        if wants_cases and "search_cases" not in completed and any(name in completed for name in ("compare_languages", "compare_domains", "rank_dimensions", "get_metrics")):
            requested = self._requested_limit(lower, 5)
            calls = []
            target_languages = languages
            if not target_languages:
                comparison = next((item for item in observations if item.tool_name in {"compare_languages", "rank_dimensions"} and item.rows), None)
                target_languages = [row["language"] for row in comparison.rows[:2]] if comparison and "language" in comparison.rows[0] else []
            if target_languages:
                for language in target_languages[:4]:
                    calls.append(ToolCall(name="search_cases", arguments={"languages": [language], "domains": domains, "result": "error", "limit": requested}, purpose=f"Retrieve {requested} error examples for {language}"))
            else:
                calls.append(ToolCall(name="search_cases", arguments={"languages": languages, "domains": domains, "result": "error", "limit": requested}, purpose="Retrieve representative error cases"))
            return PlanningDecision.execute(
                AgentPlan(
                    goal="Collect examples after metric analysis",
                    calls=calls,
                    rationale="Observation-driven second step",
                ),
                "Metrics are available; collect requested examples.",
                understanding,
            )

        return PlanningDecision.answer(
            "Existing observations are sufficient for a grounded answer.",
            understanding,
        )

    @classmethod
    def understand(
        cls,
        text: str,
        context: ConversationContext,
    ) -> TaskUnderstanding:
        lower = text.casefold()
        languages = cls.extract_languages(text)
        domains = cls.extract_domains(text)
        if cls._uses_context(lower):
            languages = languages or list(context.selected_languages)
            domains = domains or list(context.selected_domains)
        stable = _STABLE_CASE_RE.search(text)
        case = _CASE_RE.search(text)
        issue = _ISSUE_RE.search(text)
        nlu_error = _NLU_ERROR_RE.search(text)
        nlu_request = cls._is_nlu(lower)
        governance = any(
            token in lower
            for token in (
                "治理",
                "质量问题",
                "扫描数据质量",
                "变更申请",
                "确认变更",
                "审批",
                "发布",
                "回滚",
                "governance",
                "change request",
                "标注问题",
                "标签问题",
                "label quality",
                "annotation issue",
            )
        ) or issue is not None
        knowledge = any(
            token in lower
            for token in (
                "定义",
                "怎么算",
                "怎么计算",
                "如何计算",
                "含义",
                "什么是",
                "口径",
                "policy",
                "definition",
            )
        )
        if nlu_request and any(
            token in lower for token in ("准确率", "错误率", "accuracy", "errors")
        ) and not any(
            token in lower
            for token in (
                "什么是",
                "为什么",
                "如何定义",
                "definition",
                "数据口径",
                "口径差异",
            )
        ):
            knowledge = False
        case_intent = (
            stable is not None
            or case is not None
            or nlu_error is not None
            or cls._is_search(lower)
            or (nlu_request and "列出" in lower)
        )
        metric_intent = (
            cls._is_metrics(lower)
            or cls._is_ranking(lower)
            or cls._is_compare(lower)
            or cls._is_overview(lower)
            or (nlu_request and not governance and not case_intent)
        )
        if (
            knowledge
            and not languages
            and not domains
            and not cls._is_compare(lower)
            and not cls._is_ranking(lower)
            and not any(token in lower for token in ("是多少", "多少条", "表现"))
        ):
            metric_intent = False
        active_intents = sum((governance, knowledge, case_intent, metric_intent))
        if active_intents > 1:
            intent = "mixed"
        elif governance:
            intent = "data_governance"
        elif knowledge:
            intent = "knowledge_qa"
        elif case_intent:
            intent = "case_investigation"
        elif metric_intent:
            intent = "metric_analysis"
        else:
            intent = "out_of_scope"
        metrics = []
        if "准确率" in lower or "accuracy" in lower:
            metrics.append("accuracy")
        if "错误率" in lower or "error rate" in lower:
            metrics.append("error_rate")
        if (
            "错误数" in lower
            or "多少条错误" in lower
            or "errors" in lower
        ):
            metrics.append("errors")
        if "总数" in lower or "total" in lower:
            metrics.append("total")
        metric = metrics[0] if metrics else None
        source_scopes = []
        mentions_csv = "csv" in lower
        mentions_json = "json" in lower
        mentions_asr = "asr" in lower or "语音识别" in lower
        if nlu_request:
            source_scopes.append("nlu_evaluation_report")
        if mentions_csv or (
            (metric_intent or case_intent)
            and not mentions_json
            and (not nlu_request or mentions_asr)
        ):
            source_scopes.append("csv_cases")
        if mentions_json:
            source_scopes.append("json_summary")
        source_scope = source_scopes[0] if source_scopes else None
        risk_level = (
            "high"
            if any(token in lower for token in ("审批", "确认变更", "发布", "回滚", "直接修改"))
            else "medium"
            if governance
            else "low"
        )
        return TaskUnderstanding(
            intent=intent,
            languages=languages,
            domains=domains,
            metric=metric,
            metrics=metrics,
            case_id=(
                stable.group(0).casefold()
                if stable
                else nlu_error.group(0).upper()
                if nlu_error
                else None
            ),
            case_no=f"ASR #{case.group(1)}/{case.group(2)}" if case else None,
            issue_id=issue.group(0).upper() if issue else None,
            source_scope=source_scope,
            source_scopes=source_scopes,
            risk_level=risk_level,
            needs_clarification=False,
        )

    def _initial_call(
        self,
        text: str,
        lower: str,
        languages: List[str],
        domains: List[str],
        context: ConversationContext,
    ) -> ToolCall:
        issue_match = _ISSUE_RE.search(text)
        if issue_match:
            return ToolCall(
                name="get_governance_issue",
                arguments={"issue_id": issue_match.group(0).upper()},
                purpose="Retrieve the tracked governance issue",
            )
        if self._is_nlu(lower):
            return self._nlu_initial_call(text, lower, languages, domains)
        if (
            ("扫描" in lower and any(token in lower for token in ("质量", "治理")))
            or any(
            token in lower
            for token in (
                "扫描数据质量",
                "治理扫描",
                "重新扫描",
                "当前数据有哪些质量问题",
                "发现数据问题",
                "scan data quality",
            )
            )
        ):
            return ToolCall(
                name="governance_scan",
                arguments={"provider": "multilingual_asr"},
                purpose="Scan the registered data contract and persist governance issues",
            )
        if any(
            token in lower
            for token in (
                "治理问题",
                "质量问题状态",
                "待处理问题",
                "open issues",
            )
        ):
            return ToolCall(
                name="list_governance_issues",
                arguments={"provider": "multilingual_asr", "limit": 100},
                purpose="List tracked governance issues",
            )
        if any(
            token in lower
            for token in ("变更申请", "待确认变更", "待审批变更", "change requests")
        ):
            return ToolCall(
                name="list_change_requests",
                arguments={"provider": "multilingual_asr", "limit": 100},
                purpose="List governed data changes and confirmation status",
            )
        if any(token in lower for token in ("数据质量", "缺失", "异常数据", "quality issue")):
            return ToolCall(name="data_quality", arguments={"language": languages[0] if len(languages) == 1 else None}, purpose="Inspect ingestion quality warnings")
        if ("csv" in lower and "json" in lower) or any(token in lower for token in ("来源口径", "口径差异", "source scope")):
            return ToolCall(name="compare_source_scopes", arguments={"language": languages[0] if len(languages) == 1 else None, "domain": domains[0] if len(domains) == 1 else None}, purpose="Compare source scopes")
        if any(token in lower for token in ("原始 json", "json 汇总", "原始 output", "output 汇总")) and self._is_metrics(lower):
            return ToolCall(
                name="get_metrics",
                arguments={
                    "language": languages[0] if len(languages) == 1 else None,
                    "domain": domains[0] if len(domains) == 1 else None,
                    "source_scope": "json_summary",
                },
                purpose="Calculate metrics from raw JSON run summaries",
            )
        if any(token in lower for token in ("定义", "怎么算", "怎么计算", "如何计算", "含义", "数据范围", "什么是", "definition", "policy")):
            return ToolCall(name="search_knowledge", arguments={"query": text, "limit": 5}, purpose="Retrieve business definitions")
        stable = _STABLE_CASE_RE.search(text)
        case = _CASE_RE.search(text)
        if stable or case:
            arguments: Dict[str, Any] = {}
            if stable:
                arguments["case_id"] = stable.group(0)
            else:
                arguments["case_no"] = f"ASR #{case.group(1)}/{case.group(2)}"
            if len(languages) == 1:
                arguments["language"] = languages[0]
            if len(domains) == 1:
                arguments["domain"] = domains[0]
            return ToolCall(name="get_case_detail", arguments=arguments, purpose="Retrieve exact case detail")
        if self._is_ranking(lower):
            dimension = "domain" if any(token in lower for token in ("domain", "领域")) else "language"
            metric = "accuracy" if "准确率" in lower or "accuracy" in lower else "errors" if "错误数" in lower or "数量" in lower else "error_rate"
            return ToolCall(name="rank_dimensions", arguments={"dimension": dimension, "metric": metric, "language": languages[0] if len(languages) == 1 else None, "domain": domains[0] if len(domains) == 1 else None, "limit": 10}, purpose=f"Rank {dimension} by {metric}")
        if self._is_compare(lower):
            if len(languages) > 1 or any(token in lower for token in ("语言", "语种", "language")):
                return ToolCall(name="compare_languages", arguments={"languages": languages, "domain": domains[0] if len(domains) == 1 else None}, purpose="Compare language metrics")
            if len(domains) > 1 or any(token in lower for token in ("domain", "领域")):
                return ToolCall(name="compare_domains", arguments={"language": languages[0] if len(languages) == 1 else None}, purpose="Compare domain metrics")
        if self._is_search(lower):
            return ToolCall(name="search_cases", arguments={"query": self.extract_search_query(text), "languages": languages, "domains": domains, "result": "error", "limit": self._requested_limit(lower, 20)}, purpose="Search matching cases")
        if languages or domains:
            return ToolCall(name="get_metrics", arguments={"language": languages[0] if len(languages) == 1 else None, "domain": domains[0] if len(domains) == 1 else None, "source_scope": "csv_cases"}, purpose="Calculate filtered metrics")
        if self._is_overview(lower):
            return ToolCall(name="dataset_overview", purpose="Summarize the complete dataset")
        return ToolCall(
            name="platform_capabilities",
            purpose="Explain the supported data-analysis scope without inventing an answer",
        )

    def _nlu_initial_call(
        self,
        text: str,
        lower: str,
        languages: List[str],
        domains: List[str],
    ) -> ToolCall:
        error_id = _NLU_ERROR_RE.search(text)
        if error_id:
            return ToolCall(
                name="get_nlu_error_detail",
                arguments={"error_id": error_id.group(0).upper()},
                purpose="Retrieve one exact NLU model error",
            )
        if ("asr" in lower or "语音识别" in lower) and any(
            token in lower for token in ("比较", "对比", "compare", "整体", "总体")
        ):
            return ToolCall(
                name="nlu_report_overview",
                purpose="Read the NLU baseline for cross-provider comparison",
            )
        if any(
            token in lower
            for token in (
                "标注问题",
                "标签问题",
                "标注质量",
                "语言命名",
                "数值槽位",
                "label quality",
            )
        ):
            arguments: Dict[str, Any] = {"limit": 20}
            if "语言命名" in lower:
                arguments["issue_kind"] = "language_naming"
            elif "数值槽位" in lower:
                arguments["issue_kind"] = "numeric_slot_type"
            return ToolCall(
                name="nlu_label_quality",
                arguments=arguments,
                purpose="Inspect NLU label-quality findings",
            )
        if any(token in lower for token in ("错误分布", "错误类型", "breakdown")):
            group_by = (
                "intent"
                if "intent" in lower or "意图" in lower
                else "domain"
                if "domain" in lower or "领域" in lower
                else "language"
                if "语言" in lower or "语种" in lower
                else "error_type"
            )
            return ToolCall(
                name="nlu_error_breakdown",
                arguments={"group_by": group_by, "limit": 50},
                purpose=f"Aggregate NLU model errors by {group_by}",
            )
        if self._is_search(lower) or any(
            token in lower for token in ("解析失败", "模型错误", "错误明细")
        ):
            arguments = {
                "language": languages[0] if len(languages) == 1 else None,
                "domain": domains[0] if len(domains) == 1 else None,
                "error_type": (
                    "parse_failure"
                    if "解析失败" in lower
                    else "intent"
                    if "intent" in lower or "意图错误" in lower
                    else "slots"
                    if "slot" in lower or "槽位错误" in lower
                    else "domain"
                    if "domain错误" in lower or "领域错误" in lower
                    else None
                ),
                "limit": self._requested_limit(lower, 20),
            }
            quoted = self.extract_search_query(text)
            if quoted and quoted.casefold() not in {"nlu", "intent", "slot", "slots"}:
                arguments["query"] = quoted
            return ToolCall(
                name="search_nlu_errors",
                arguments=arguments,
                purpose="Search the NLU model-error detail subset",
            )
        if self._is_ranking(lower) or self._is_compare(lower) or languages or domains:
            dimension = (
                "domain"
                if domains or any(token in lower for token in ("domain", "领域"))
                else "language"
            )
            names = domains if dimension == "domain" else languages
            return ToolCall(
                name="nlu_compare_accuracy",
                arguments={
                    "dimension": dimension,
                    "order": "best" if any(token in lower for token in ("最高", "最好")) else "worst",
                    "names": names,
                    "limit": 20,
                },
                purpose=f"Compare NLU exact-match accuracy by {dimension}",
            )
        return ToolCall(
            name="nlu_report_overview",
            purpose="Summarize the offline NLU re-evaluation report",
        )

    @staticmethod
    def extract_languages(text: str) -> List[str]:
        lower = text.casefold()
        return [language for language, aliases in _LANGUAGE_ALIASES.items() if any(alias in lower for alias in aliases)]

    @staticmethod
    def extract_domains(text: str) -> List[str]:
        lower = text.casefold()
        result = [domain for domain, aliases in _DOMAIN_ALIASES.items() if any(alias in lower for alias in aliases)]
        if re.search(r"\bphone\b", lower) and "phone number" not in lower and "phonebook" not in lower and "phone" not in result:
            result.append("phone")
        return result

    @staticmethod
    def extract_search_query(text: str) -> str:
        quoted = re.search(r"[`'\"]([^`'\"]+)[`'\"]", text)
        if quoted:
            return quoted.group(1).strip()
        patterns = [
            r"(?:包含|含有|出现)\s*([^，。？！?]+?)(?:的)?(?:错误|案例|样本)?$",
            r"(?:搜索|查找|找出)\s*([^，。？！?]+?)(?:的)?(?:错误|案例|样本)?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                value = re.sub(r"^(?:所有|一下)\s*", "", value)
                value = re.sub(r"\s*的?(?:错误|案例|样本)+$", "", value)
                value = re.sub(r"\s*的?$", "", value)
                return re.sub(r"\s*(?:相关)?$", "", value)
        english_terms = re.findall(r"[A-Za-z][A-Za-z0-9_.-]+", text)
        excluded = {"asr", "ref", "hyp", "case", "cases"}
        return next((term for term in english_terms if term.casefold() not in excluded), "")

    @staticmethod
    def _uses_context(lower: str) -> bool:
        return any(token in lower for token in ("这个", "该语言", "该领域", "其中", "里面", "刚才", "这些"))

    @staticmethod
    def _is_nlu(lower: str) -> bool:
        return any(
            token in lower
            for token in ("nlu", "语义理解", "意图识别", "槽位识别")
        )

    @staticmethod
    def _is_search(lower: str) -> bool:
        return any(
            token in lower
            for token in ("搜索", "查找", "找出", "包含", "案例", "样本", "search")
        )

    @staticmethod
    def _is_compare(lower: str) -> bool:
        return any(token in lower for token in ("比较", "对比", "分别", "差异", "compare"))

    @staticmethod
    def _is_metrics(lower: str) -> bool:
        return any(
            token in lower
            for token in ("多少", "准确率", "错误率", "错误数", "总数", "指标", "metrics")
        )

    @staticmethod
    def _is_ranking(lower: str) -> bool:
        return any(
            token in lower
            for token in (
                "最高",
                "最低",
                "最多",
                "最少",
                "排名",
                "最好",
                "最差",
                "哪个",
                "rank",
                "highest",
                "lowest",
                "most",
                "least",
            )
        )

    @staticmethod
    def _is_overview(lower: str) -> bool:
        return any(
            token in lower
            for token in (
                "多少条数据",
                "多少数据",
                "数据总量",
                "整体数据",
                "总体数据",
                "数据概览",
                "整体表现",
                "总体表现",
                "当前接入",
                "一共接入",
                "总共有多少",
                "七种语言表现",
                "dataset overview",
            )
        )

    @staticmethod
    def _requested_limit(lower: str, default: int) -> int:
        match = re.search(r"(?:各自|分别|前)?\s*(\d+)\s*(?:个|条)", lower)
        return max(1, min(int(match.group(1)), 50)) if match else default


class AzurePlanner(Planner):
    """LLM planner constrained by the provider tool catalog and observations."""

    def __init__(
        self,
        tools: Sequence[Dict[str, Any]],
        skills: SkillManager,
        gateway: AzureLLMGateway,
    ) -> None:
        self.tools = list(tools)
        self.tool_names = {tool["name"] for tool in tools}
        self.skills = skills
        self.gateway = gateway
        self.fallback = OfflinePlanner(tools)

    def next_step(self, question: str, context: ConversationContext, observations: Sequence[ToolObservation], round_number: int) -> PlanningDecision:
        if round_number >= 3:
            return PlanningDecision.answer("Maximum planning rounds reached.")
        payload = {
            "question": question,
            "context": context.model_dump(mode="json"),
            "round": round_number,
            "tools": self.tools,
            "observations": [item.model_dump(mode="json") for item in observations],
            "output_schema": PlanningDecision.model_json_schema(),
            "skills": self.skills.prompt_for(question, "orchestrator"),
        }
        system = (
            "You are the orchestrator of a business data analysis agent. Decide the next step from current observations. "
            "Use tools for every business fact and number. Execute independent calls together. Do not repeat a successful call. "
            "Choose status=answer only when observations are sufficient. Use at most 3 rounds. Return JSON only."
        )
        try:
            content = self.gateway.chat(
                "planning",
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            raw = json.loads(content)
            decision = PlanningDecision.model_validate(raw)
            if decision.understanding is None:
                decision = decision.model_copy(
                    update={
                        "understanding": OfflinePlanner.understand(question, context)
                    }
                )
            if decision.plan:
                unknown = [call.name for call in decision.plan.calls if call.name not in self.tool_names]
                if unknown:
                    raise ValueError(f"Planner selected unknown tools: {unknown}")
            return decision
        except Exception as error:
            fallback = self.fallback.next_step(
                question,
                context,
                observations,
                round_number,
            )
            return fallback.model_copy(
                update={
                    "reason": (
                        "Azure planning failed; deterministic fallback used: "
                        f"{type(error).__name__}"
                    )
                }
            )
