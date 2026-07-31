"""Streamlit workbench for intelligent cockpit voice quality data."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from data_insight.config import Settings
from data_insight.evaluation import AgentEvaluator
from data_insight.export import answer_csv_bytes
from data_insight.llm import AzureLLMConfig
from data_insight.schemas import AgentAnswer
from data_insight.service import AgentService

st.set_page_config(
    page_title="智能座舱语音质量数据分析与治理 Agent",
    page_icon=":material/analytics:",
    layout="wide",
    initial_sidebar_state="auto",
)

st.markdown(
    """
    <style>
    :root {
      --ink: #17211d;
      --muted: #65716b;
      --line: #d7ddd9;
      --paper: #f7f9f7;
      --surface: #ffffff;
      --green: #176b55;
      --green-soft: #e7f1ed;
      --amber: #98601d;
      --amber-soft: #fff1dc;
      --red: #9c403b;
      --red-soft: #fae8e6;
    }
    .stApp {
      color: var(--ink);
      background-color: var(--paper);
      background-image:
        linear-gradient(rgba(23,107,85,.024) 1px, transparent 1px),
        linear-gradient(90deg, rgba(23,107,85,.018) 1px, transparent 1px);
      background-size: 32px 32px;
      font-family: "Bahnschrift", "Aptos", sans-serif;
    }
    header[data-testid="stHeader"] { background: transparent; }
    .block-container { max-width: 1440px; padding-top: 1.4rem; padding-bottom: 4rem; }
    section[data-testid="stSidebar"] { background: #eef2ef; border-right: 1px solid var(--line); }
    h1, h2, h3 { letter-spacing: 0; color: var(--ink); }
    h1 { font-size: 1.65rem !important; }
    h2 { font-size: 1.18rem !important; margin-top: 1.2rem !important; }
    h3 { font-size: .98rem !important; }
    .masthead { border-bottom: 1px solid var(--line); padding-bottom: 1rem; margin-bottom: 1rem; }
    .eyebrow { color: var(--green); font-size: .72rem; font-weight: 800; text-transform: uppercase; }
    .title { font-size: 1.55rem; line-height: 1.35; font-weight: 760; margin-top: .18rem; }
    .subtitle { color: var(--muted); font-size: .9rem; margin-top: .28rem; }
    .scope-strip { display:flex; flex-wrap:wrap; gap:.55rem 1.2rem; padding:.65rem 0; color:var(--muted); font-size:.8rem; }
    .scope-strip strong { color:var(--ink); }
    .agent-status { border-left:3px solid var(--green); background:var(--green-soft); padding:.72rem .85rem; font-size:.84rem; margin:.75rem 0; }
    .warning-status { border-left-color:var(--amber); background:var(--amber-soft); }
    [data-testid="stChatMessage"] { background:rgba(255,255,255,.9); border:1px solid var(--line); border-radius:6px; padding:.35rem .55rem; }
    [data-testid="stMetric"] { background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:.8rem 1rem; min-height:100px; }
    .trace-row { display:grid; grid-template-columns:100px 160px 1fr; gap:.8rem; border-bottom:1px solid var(--line); padding:.62rem 0; font-size:.82rem; }
    .trace-kind { color:var(--green); font-weight:760; text-transform:uppercase; }
    .trace-name { font-weight:700; }
    .source-row { border-top:1px solid var(--line); padding:.55rem 0; font-size:.8rem; }
    .source-row code { color:var(--muted); }
    .grounded { color:var(--green); font-weight:760; }
    .not-grounded { color:var(--red); font-weight:760; }
    div.stButton > button, div.stDownloadButton > button { border-radius:4px; font-weight:650; }
    div[data-baseweb="select"] > div, div[data-baseweb="input"] > div, textarea { border-radius:4px !important; }
    [data-testid="stDataFrame"] { border:1px solid var(--line); }
    @media (max-width: 760px) {
      .trace-row { grid-template-columns:1fr; gap:.2rem; }
      .block-container { padding-left:1rem; padding-right:1rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def get_service(mode: str) -> AgentService:
    return AgentService(mode=mode)


def render_header() -> None:
    st.markdown(
        """
        <div class="masthead">
          <div class="eyebrow">Intelligent Cockpit Voice Quality Data Agent</div>
          <div class="title">智能座舱多语言语音质量数据分析与治理 Agent</div>
                    <div class="subtitle">任务编排 Agent 协调只读数据分析 Agent 与数据治理 Agent；当前接入七语种 ASR 明细和 NLU 离线重测报告两个真实 Provider，变更由用户检查 Diff 后显式确认并生成可回滚版本。</div>
          <div class="scope-strip">
                        <span><strong>2</strong> Providers</span>
                        <span><strong>92,301</strong> ASR Cases</span>
                        <span><strong>104,897</strong> NLU Samples</span>
            <span><strong>7</strong> Languages</span>
            <span><strong>6</strong> Domains</span>
            <span>DuckDB + LangGraph + FTS5 + SQLite</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def sidebar() -> str:
    st.sidebar.subheader("Agent 设置")
    settings = Settings.load()
    llm_config = AzureLLMConfig.load(settings.project_root)
    azure_ready = llm_config.configured and (
        llm_config.auth_mode != "entra" or llm_config.entra_login_cached
    )
    options = ["离线确定性 Agent"] + (["Azure OpenAI Agent"] if azure_ready else [])
    label = st.sidebar.radio("运行模式", options)
    mode = "azure" if label == "Azure OpenAI Agent" else "offline"
    st.sidebar.caption("离线模式使用相同 LangGraph 和工具链，只将 Planner/Composer 替换为确定性实现。")
    if llm_config.configured and not azure_ready:
        st.sidebar.info(
            "Azure 配置已完成，但尚未保存 Entra 登录。请在项目终端运行 "
            "`data-agent llm login`。"
        )
    elif not azure_ready:
        st.sidebar.info(
            "Azure 尚未配置。运行 `data-agent llm init`，然后在项目 `.env` 中填写 "
            "Endpoint、API Key 和 Deployment。"
        )
        with st.sidebar.expander("查看缺失配置"):
            for error in llm_config.errors:
                st.caption(f"- {error}")
    else:
        st.sidebar.success(
            f"Azure 配置已就绪 · `{llm_config.deployment}`"
        )
    if st.sidebar.button("清空当前对话", icon=":material/delete:", width="stretch"):
        st.session_state.messages = []
        st.session_state.conversation_id = None
        st.session_state.last_trace_id = None
        st.rerun()
    return mode


def render_answer(answer: AgentAnswer, index: int) -> None:
    st.markdown(answer.answer_markdown)
    status_class = "grounded" if answer.grounded else "not-grounded"
    status_text = "Grounded 校验通过" if answer.grounded else "Grounding 未通过"
    st.markdown(
        f'<div class="agent-status"><span class="{status_class}">{status_text}</span> · '
        f'{len(answer.agents_used)} 个 Agent · {len(answer.tools_used)} 次工具调用 · '
        f'Trace <code>{answer.trace_id}</code><br>'
        f'<strong>协作链：</strong> {" → ".join(answer.agents_used)}</div>',
        unsafe_allow_html=True,
    )
    downloads = st.columns(2)
    downloads[0].download_button(
        "下载 JSON",
        data=json.dumps(
            answer.model_dump(mode="json"), ensure_ascii=False, indent=2
        ).encode("utf-8"),
        file_name=f"agent-result-{answer.trace_id}.json",
        mime="application/json",
        icon=":material/download:",
        key=f"answer-json-{index}-{answer.trace_id}",
        width="stretch",
    )
    downloads[1].download_button(
        "下载 CSV",
        data=answer_csv_bytes(answer),
        file_name=f"agent-result-{answer.trace_id}.csv",
        mime="text/csv",
        icon=":material/download:",
        key=f"answer-csv-{index}-{answer.trace_id}",
        width="stretch",
    )
    with st.expander("查看工具 Observation 与数据来源"):
        if answer.specialist_results:
            st.markdown("#### 专业 Agent 结果")
            for result in answer.specialist_results:
                status = "成功" if result.success else "失败"
                st.markdown(
                    f"- **{result.agent}** · {status} · {result.summary or result.error}"
                )
        for observation in answer.observations:
            st.markdown(
                f"**`{observation.tool_name}`** · {observation.elapsed_ms:.2f} ms"
                + (" · cache" if observation.cached else "")
            )
            if observation.rows:
                st.dataframe(pd.DataFrame(observation.rows), hide_index=True, width="stretch")
            elif observation.data:
                st.json(observation.data, expanded=False)
        st.markdown("#### 来源")
        for source in answer.sources:
            st.markdown(
                f'<div class="source-row"><strong>{source.label}</strong> · {source.scope}<br><code>{source.path}</code></div>',
                unsafe_allow_html=True,
            )
        for warning in answer.warnings:
            st.warning(warning)


def render_chat(mode: str) -> None:
    st.markdown(
        '<div class="agent-status">执行链：理解目标 → 规划工具 → 执行与观察 → 重新规划 → Grounding 校验 → 回答</div>',
        unsafe_allow_html=True,
    )
    messages = st.session_state.setdefault("messages", [])
    st.session_state.setdefault("conversation_id", None)
    if not messages:
        st.subheader("试试这些多步骤问题")
        suggestions = [
            "七种语言中哪个错误率最高？",
            "比较德语和法语 mediaControl 的错误率，并分别列出各自 3 条错误案例",
            "找出英语中包含 TuneIn 的错误案例",
            "法语 carControl 的 CSV 和 JSON 口径有什么差异？",
            "准确率指标是怎么计算的？",
            "当前数据有哪些质量问题？",
            "NLU 修正标签口径后的整体准确率是多少？",
            "NLU 哪种错误类型最多？",
            "NLU 有哪些标签质量问题？",
            "比较 ASR 和 NLU 的整体准确率及数据口径",
        ]
        columns = st.columns(2)
        for index, question in enumerate(suggestions):
            if columns[index % 2].button(question, key=f"suggestion-{index}", width="stretch"):
                st.session_state.pending_question = question
                st.rerun()

    for index, message in enumerate(messages):
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.markdown(message["content"])
            else:
                render_answer(AgentAnswer.model_validate(message["answer"]), index)

    pending = st.session_state.pop("pending_question", None)
    prompt = st.chat_input("输入跨语言指标、案例、口径或业务定义问题")
    question = pending or prompt
    if not question:
        return
    messages.append({"role": "user", "content": question})
    try:
        service = get_service(mode)
        with st.spinner("Agent 正在规划并执行数据工具..."):
            answer = service.ask(question, st.session_state.conversation_id)
        st.session_state.conversation_id = answer.conversation_id
        st.session_state.last_trace_id = answer.trace_id
        messages.append({"role": "assistant", "answer": answer.model_dump(mode="json")})
    except Exception as error:
        st.error(str(error))
    st.rerun()


def render_trace(mode: str) -> None:
    trace_id = st.text_input("Trace ID", value=st.session_state.get("last_trace_id") or "")
    if not trace_id:
        st.info("先在分析工作台执行一个问题，或输入已有 Trace ID。")
        return
    events = get_service(mode).trace(trace_id)
    if not events:
        st.warning("没有找到该 Trace。")
        return
    st.subheader("Agent 执行轨迹")
    for event in events:
        st.markdown(
            f'<div class="trace-row"><div class="trace-kind">{event.event_type}</div><div class="trace-name">{event.name}</div><div>{event.created_at}</div></div>',
            unsafe_allow_html=True,
        )
        with st.expander(f"{event.event_type} · {event.name} 详情"):
            st.json(event.payload, expanded=False)


def render_governance(mode: str) -> None:
    service = get_service(mode)
    active = service.active_dataset_version()
    header_columns = st.columns([1.4, 1, 1])
    header_columns[0].subheader("数据治理")
    header_columns[1].metric(
        "Active Version", active.version_id if active else "raw"
    )
    if header_columns[2].button(
        "扫描数据质量", icon=":material/fact_check:", width="stretch"
    ):
        with st.spinner("数据治理 Agent 正在按数据契约扫描..."):
            observation = service.governance_scan()
        st.session_state.governance_scan = observation.model_dump(mode="json")
        st.rerun()

    st.markdown(
        '<div class="agent-status">治理原则：raw 数据永不覆盖；Agent 只发现问题和创建草稿；用户检查 Diff 与契约结果后确认，再生成可回滚的数据版本。</div>',
        unsafe_allow_html=True,
    )
    if scan := st.session_state.get("governance_scan"):
        st.success(
            f"最近扫描发现 {scan['data']['finding_count']} 个治理候选；原始文件未修改。"
        )

    filter_columns = st.columns(2)
    status_filter = filter_columns[0].selectbox(
        "Issue 状态", ["全部", "OPEN", "IN_REVIEW", "RESOLVED", "WAIVED"]
    )
    severity_filter = filter_columns[1].selectbox(
        "严重级别", ["全部", "critical", "error", "warning", "info"]
    )
    issues = service.governance_issues(
        status=None if status_filter == "全部" else status_filter,
        severity=None if severity_filter == "全部" else severity_filter,
    )
    st.subheader("质量 Issue")
    if not issues:
        st.info("当前筛选范围没有治理 Issue。先运行数据质量扫描。")
    else:
        issue_rows = [
            {
                "Issue": item.issue_id,
                "级别": item.finding.severity,
                "规则": item.finding.rule_id,
                "实体": item.finding.entity_key,
                "字段": item.finding.field_name or "-",
                "状态": item.status,
                "说明": item.finding.detail,
            }
            for item in issues
        ]
        st.dataframe(
            pd.DataFrame(issue_rows), hide_index=True, width="stretch", height=360
        )
        selected_issue_id = st.selectbox(
            "选择 Issue",
            [item.issue_id for item in issues],
            format_func=lambda issue_id: next(
                f"{item.finding.severity} · {item.finding.rule_id} · {item.finding.entity_key}"
                for item in issues
                if item.issue_id == issue_id
            ),
        )
        selected_issue = next(
            item for item in issues if item.issue_id == selected_issue_id
        )
        with st.expander("Issue 证据", expanded=True):
            st.json(selected_issue.model_dump(mode="json"), expanded=False)

        if selected_issue.finding.field_name and selected_issue.status == "OPEN":
            st.markdown("#### 创建变更草稿")
            with st.form(f"change-draft-{selected_issue.issue_id}"):
                field_name = selected_issue.finding.field_name
                if field_name == "result_raw":
                    proposed_value = st.selectbox("建议值", ["✗", "✓"])
                else:
                    proposed_value = st.text_area(
                        "建议值", value=str(selected_issue.finding.current_value or "")
                    )
                draft_columns = st.columns(2)
                requested_by = draft_columns[0].text_input(
                    "操作记录", value="local-user"
                )
                reason = draft_columns[1].text_input("变更理由")
                create_draft = st.form_submit_button(
                    "创建 Draft", icon=":material/edit_document:"
                )
            if create_draft:
                if not requested_by.strip() or not reason.strip():
                    st.warning("请填写操作记录和变更理由。")
                else:
                    try:
                        change = service.create_change_draft(
                            selected_issue.issue_id,
                            proposed_value,
                            reason.strip(),
                            requested_by.strip(),
                        )
                        st.session_state.selected_change_id = change.change_id
                        st.success(f"已创建变更草稿 {change.change_id}")
                        st.rerun()
                    except Exception as error:
                        st.error(str(error))
        elif not selected_issue.finding.field_name:
            st.info("这是范围级或跨来源问题，不能直接改单个字段；需由数据 Owner 提供修正版源文件或确认权威口径。")

    changes = service.governance_changes()
    st.subheader("变更建议、人工确认与发布")
    if not changes:
        st.caption("尚无变更申请。")
    else:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Change": item.change_id,
                        "Issue": item.issue_id,
                        "字段": item.field_name,
                        "Before": item.before_value,
                        "After": item.proposed_value,
                        "状态": item.status,
                        "创建人": item.requested_by,
                        "确认人": item.reviewed_by or "-",
                    }
                    for item in changes
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        default_change = st.session_state.get("selected_change_id")
        change_ids = [item.change_id for item in changes]
        selected_change_id = st.selectbox(
            "选择变更",
            change_ids,
            index=change_ids.index(default_change) if default_change in change_ids else 0,
        )
        change = next(item for item in changes if item.change_id == selected_change_id)
        preview = service.preview_change(change.change_id)
        with st.expander("Diff Preview", expanded=True):
            st.json(preview.data if preview.success else {"error": preview.error})

        if change.status in {"DRAFT", "PENDING_APPROVAL"}:
            with st.form(f"confirm-{change.change_id}"):
                confirm_columns = st.columns(2)
                actor = confirm_columns[0].text_input(
                    "确认记录", value=change.requested_by or "local-user"
                )
                comment = confirm_columns[1].text_input(
                    "确认备注", value="已检查 Diff 与数据契约校验结果"
                )
                reviewed = st.checkbox("我已检查 Before/After Diff 和契约校验结果")
                confirmed = st.form_submit_button(
                    "确认变更", type="primary", icon=":material/check_circle:"
                )
            if confirmed:
                if not reviewed:
                    st.warning("请先检查并勾选 Diff 与契约校验确认。")
                elif not actor.strip() or not comment.strip():
                    st.warning("确认记录和备注不能为空。")
                else:
                    try:
                        service.confirm_change(
                            change.change_id, actor.strip(), comment.strip()
                        )
                        st.rerun()
                    except Exception as error:
                        st.error(str(error))
        elif change.status in {"CONFIRMED", "APPROVED"}:
            with st.form(f"publish-{change.change_id}"):
                publisher = st.text_input(
                    "发布记录",
                    value=change.reviewed_by or change.requested_by or "local-user",
                )
                publish = st.form_submit_button(
                    "发布新数据版本", type="primary", icon=":material/publish:"
                )
            if publish:
                if not publisher.strip():
                    st.warning("请填写发布人。")
                else:
                    try:
                        version = service.publish_changes(
                            [change.change_id], publisher.strip()
                        )
                        st.success(f"已发布 {version.version_id}")
                        st.rerun()
                    except Exception as error:
                        st.error(str(error))

    active = service.active_dataset_version()
    if active and active.parent_version:
        st.subheader("版本回滚")
        with st.form("rollback-active-version"):
            rollback_actor = st.text_input("回滚操作人")
            rollback = st.form_submit_button(
                f"回滚 {active.version_id} → {active.parent_version}",
                icon=":material/restore:",
            )
        if rollback:
            if not rollback_actor.strip():
                st.warning("请填写回滚操作人。")
            else:
                try:
                    version = service.rollback_active_version(rollback_actor.strip())
                    st.success(f"已回滚至 {version.version_id}")
                    st.rerun()
                except Exception as error:
                    st.error(str(error))

    with st.expander("Governance Audit Log"):
        audit = service.governance_audit(100)
        if audit:
            audit_rows = [
                {
                    key: (
                        json.dumps(value, ensure_ascii=False, sort_keys=True)
                        if isinstance(value, (dict, list))
                        else value
                    )
                    for key, value in item.items()
                    if key != "detail_json"
                }
                for item in audit
            ]
            st.dataframe(
                pd.DataFrame(audit_rows), hide_index=True, width="stretch"
            )
        else:
            st.caption("尚无审计记录。")


def render_system(mode: str) -> None:
    service = get_service(mode)
    health = service.health()
    provider = health["provider"]
    provider_states = {
        item["provider"]: item for item in provider["providers"]
    }
    asr = provider_states["multilingual_asr"]
    nlu = provider_states.get("nlu_evaluation", {})
    columns = st.columns(4)
    columns[0].metric("ASR Case", f"{asr['cases']:,}")
    columns[1].metric("ASR 错误", f"{asr['errors']:,}")
    columns[2].metric("语言 / Domain", f"{len(asr['languages'])} / {len(asr['domains'])}")
    columns[3].metric("ASR 导入问题", asr["quality_issues"])
    nlu_columns = st.columns(4)
    nlu_columns[0].metric("NLU Sample", f"{nlu.get('samples', 0):,}")
    nlu_columns[1].metric("NLU 模型错误", f"{nlu.get('model_errors', 0):,}")
    nlu_columns[2].metric(
        "NLU 修正后准确率",
        f"{nlu.get('corrected_accuracy_pct', 0):.2f}%",
    )
    nlu_columns[3].metric("NLU Intent", nlu.get("intents", 0))
    st.subheader("Provider 与工具")
    st.json(provider, expanded=False)
    st.subheader("多 Agent 团队")
    agent_rows = [
        {
            "Agent": name,
            "状态": "Ready" if detail.get("ready") else "Unavailable",
            "工具权限": ", ".join(detail.get("allowed_tools", [])) or "无业务工具",
        }
        for name, detail in health["agents"].items()
    ]
    st.dataframe(pd.DataFrame(agent_rows), hide_index=True, width="stretch")
    st.subheader("确定性治理组件")
    st.json(health.get("components", {}), expanded=False)
    st.subheader("动态 Skills")
    st.json(health["skills"], expanded=False)
    st.subheader("Tool Runtime")
    stats = service.runtime.summary()
    if stats:
        st.dataframe(pd.DataFrame.from_dict(stats, orient="index").reset_index(names="tool"), hide_index=True, width="stretch")
    else:
        st.caption("当前进程尚未执行工具。")
    st.subheader("Azure OpenAI")
    llm_status = health["llm"]["configuration"]
    if llm_status["configured"]:
        st.success(
            f"配置已就绪 · Endpoint `{llm_status['endpoint_host']}` · "
            f"Deployment `{llm_status['deployment']}`"
        )
        if st.button("测试 Azure 连接", icon=":material/network_check:"):
            try:
                with st.spinner("正在发送最小连接测试..."):
                    st.session_state.llm_test = service.test_llm_connection()
            except Exception as error:
                st.session_state.llm_test = {
                    "connected": False,
                    "error": f"{type(error).__name__}: {error}",
                }
        if result := st.session_state.get("llm_test"):
            if result.get("connected"):
                st.success(
                    f"连接成功 · {result['latency_ms']:.0f} ms · "
                    f"{result['usage']['total_tokens']} tokens"
                )
            else:
                st.error(result.get("error", "连接失败"))
    else:
        st.warning("尚未配置 Azure；离线 Agent 不受影响。")
        for error in llm_status["errors"]:
            st.caption(f"- {error}")
        st.code(
            "data-agent llm init\n"
            "# 在本机编辑 .env，不要把 API Key 发到聊天或提交 Git\n"
            "data-agent llm status\n"
            "data-agent llm test",
            language="powershell",
        )
    usage = health["llm"]["usage"]
    llm_columns = st.columns(5)
    llm_columns[0].metric("LLM 调用", usage["calls"])
    llm_columns[1].metric("成功率", f"{usage['success_rate']:.0%}")
    llm_columns[2].metric("P95 延迟", f"{usage['p95_latency_ms']:.0f} ms")
    llm_columns[3].metric("Tokens", usage["total_tokens"])
    llm_columns[4].metric("估算费用", f"${usage['estimated_cost_usd']:.2f}")
    st.caption(
        "费用按当前配置的输入/输出单价估算，仅供开发监控；Azure 账单与企业合同价格为准。"
    )
    st.subheader("端到端评测")
    settings = Settings.load()
    if mode == "azure":
        evaluation_level = st.segmented_control(
            "Azure 评测范围",
            options=("6 条在线 Smoke", "25 条完整回归"),
            default="6 条在线 Smoke",
        )
        full_confirmed = st.checkbox(
            "我确认运行 25 条完整 Azure 回归并接受相应 Token 费用",
            disabled=evaluation_level != "25 条完整回归",
        )
        dataset_name = (
            "core_questions.json"
            if evaluation_level == "25 条完整回归"
            else "azure_smoke.json"
        )
        can_run = evaluation_level != "25 条完整回归" or full_confirmed
        button_label = f"运行 {evaluation_level}"
    else:
        dataset_name = "core_questions.json"
        can_run = True
        button_label = "运行 25 条离线核心回归"
    if st.button(
        button_label,
        icon=":material/science:",
        disabled=not can_run,
    ):
        with st.spinner("正在运行真实 Agent 链路..."):
            dataset = settings.project_root / "eval" / "datasets" / dataset_name
            baseline = (
                settings.project_root
                / "eval"
                / "baselines"
                / f"{dataset.stem}.{mode}.json"
            )
            report = AgentEvaluator(
                service,
                dataset,
                judge=service.answer_judge,
            ).run(baseline_path=baseline)
        st.session_state.eval_report = report
    if report := st.session_state.get("eval_report"):
        eval_columns = st.columns(4)
        eval_columns[0].metric("通过率", f"{report['pass_rate']:.0%}")
        eval_columns[1].metric("工具准确率", f"{report['tool_accuracy']:.0%}")
        eval_columns[2].metric("Agent 准确率", f"{report['agent_accuracy']:.0%}")
        eval_columns[3].metric("答案准确率", f"{report['answer_accuracy']:.0%}")
        understanding_columns = st.columns(3)
        understanding_columns[0].metric(
            "意图准确率", f"{report['intent_accuracy']:.0%}"
        )
        understanding_columns[1].metric(
            "实体准确率", f"{report['entity_accuracy']:.0%}"
        )
        understanding_columns[2].metric(
            "引用准确率", f"{report['citation_accuracy']:.0%}"
        )
        if report.get("retrieval"):
            retrieval_columns = st.columns(3)
            retrieval_columns[0].metric(
                "RAG Recall@K", f"{report['retrieval']['recall_at_k']:.0%}"
            )
            retrieval_columns[1].metric(
                "RAG MRR", f"{report['retrieval']['mrr']:.2f}"
            )
            retrieval_columns[2].metric(
                "RAG 用例",
                f"{report['retrieval']['passed']}/{report['retrieval']['total']}",
            )
        if report.get("judge"):
            st.caption(
                "LLM-as-Judge："
                f"平均 {report['judge']['average']:.2f}/5 · "
                f"相关性 {report['judge']['relevance']:.2f} · "
                f"完整性 {report['judge']['completeness']:.2f} · "
                f"证据使用 {report['judge']['evidence_use']:.2f}"
            )
        else:
            st.caption("离线模式仅运行确定性事实评测；Azure 模式额外运行 LLM-as-Judge。")
        st.caption(
            "当前运行 Agent 端到端延迟："
            f"平均 {report['average_ms']:.0f} ms · "
            f"P50 {report['p50_ms']:.0f} ms · P95 {report['p95_ms']:.0f} ms。"
            "该结果是当前机器上的顺序回归，不代表生产 SLA。"
        )
        if report.get("llm_usage") and report["llm_usage"]["calls"]:
            run_usage = report["llm_usage"]
            run_columns = st.columns(4)
            run_columns[0].metric("本轮 LLM 调用", run_usage["calls"])
            run_columns[1].metric("本轮 Tokens", run_usage["total_tokens"])
            run_columns[2].metric("本轮 P95", f"{run_usage['p95_latency_ms']:.0f} ms")
            run_columns[3].metric(
                "本轮估算费用", f"${run_usage['estimated_cost_usd']:.3f}"
            )
        if report.get("regressions"):
            for regression in report["regressions"]:
                st.error(regression)
        elif report.get("baseline"):
            st.success("相对评测基线未发现超过 5% 的回归。")
        else:
            st.info("尚无该模式的评测基线，可通过 CLI 使用 --update-baseline 创建。")
        st.dataframe(pd.DataFrame(report["results"]), hide_index=True, width="stretch")
    with st.expander("最近评测运行"):
        runs = service.state_store.list_evaluation_runs(20)
        if runs:
            run_rows = []
            for item in runs:
                summary = item.get("summary") or {}
                judge = summary.get("judge") or {}
                llm_usage = summary.get("llm_usage") or {}
                run_rows.append(
                    {
                        "Run": item["run_id"],
                        "Dataset": item["dataset"],
                        "Mode": item["mode"],
                        "状态": item["status"],
                        "通过率": summary.get("pass_rate"),
                        "Judge": judge.get("average"),
                        "Tokens": llm_usage.get("total_tokens"),
                        "估算费用 USD": llm_usage.get("estimated_cost_usd"),
                        "开始时间": item["started_at"],
                    }
                )
            st.dataframe(pd.DataFrame(run_rows), hide_index=True, width="stretch")
        else:
            st.caption("尚无持久化评测运行。")


def main() -> None:
    render_header()
    mode = sidebar()
    view = st.segmented_control(
        "工作区",
        options=("分析工作台", "数据治理", "Agent Trace", "系统与评测"),
        default="分析工作台",
        label_visibility="collapsed",
        width="stretch",
    )
    if view == "分析工作台":
        render_chat(mode)
    elif view == "数据治理":
        render_governance(mode)
    elif view == "Agent Trace":
        render_trace(mode)
    else:
        render_system(mode)


if __name__ == "__main__":
    main()
