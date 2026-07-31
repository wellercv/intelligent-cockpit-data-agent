"""Command-line entry point for intelligent cockpit voice quality data."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict

from data_insight.config import Settings
from data_insight.evaluation import AgentEvaluator
from data_insight.llm import AzureLLMConfig, AzureLLMGateway, LLMConfigurationError
from data_insight.providers.nlu import NLUEvaluationProvider
from data_insight.service import AgentService
from data_insight.warehouse import ASRWarehouse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Intelligent Cockpit Multilingual Voice Quality Data Agent"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask = subparsers.add_parser("ask", help="ask one grounded data question")
    ask.add_argument("question", nargs="+")
    ask.add_argument("--mode", choices=("offline", "azure", "auto"), default="offline")
    ask.add_argument("--conversation-id")

    subparsers.add_parser("ingest", help="rebuild registered ASR and NLU warehouses")
    subparsers.add_parser("health", help="show provider and skill readiness")

    demo = subparsers.add_parser(
        "demo",
        help="start the workbench with deterministic synthetic ASR/NLU data",
    )
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--port", type=int, default=8501)

    evaluate = subparsers.add_parser("eval", help="run end-to-end agent evaluation")
    evaluate.add_argument("--dataset", default="core_questions.json")
    evaluate.add_argument(
        "--mode", choices=("offline", "azure", "auto"), default="offline"
    )
    evaluate.add_argument("--baseline", help="optional baseline JSON filename")
    evaluate.add_argument(
        "--update-baseline",
        action="store_true",
        help="replace the selected baseline with current metrics",
    )

    serve = subparsers.add_parser("serve", help="start FastAPI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    ui = subparsers.add_parser("ui", help="start Streamlit workbench")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8501)

    mcp = subparsers.add_parser("mcp", help="start the approved MCP tool server")
    mcp.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default="stdio",
    )
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8001)

    llm = subparsers.add_parser("llm", help="configure and test Azure OpenAI")
    llm_commands = llm.add_subparsers(dest="llm_command", required=True)
    llm_commands.add_parser("init", help="create a local .env template")
    llm_commands.add_parser(
        "login", help="perform one Entra browser login and save the account record"
    )
    llm_commands.add_parser("status", help="show safe configuration status without a network call")
    llm_commands.add_parser("test", help="send one minimal request to verify Azure connectivity")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "demo":
        os.environ["DATA_AGENT_DEMO_MODE"] = "1"
    settings = Settings.load()
    if args.command == "demo":
        print(
            "Starting Synthetic Demo mode. Displayed figures are generated fixtures, "
            "not business results."
        )
        return _run_ui(settings, args.host, args.port)
    if args.command == "ingest":
        asr_report = ASRWarehouse(settings).rebuild()
        nlu_provider = NLUEvaluationProvider(settings)
        if nlu_provider.ready:
            nlu_provider.reload()
        print(
            json.dumps(
                {
                    "asr": asdict(asr_report),
                    "nlu": nlu_provider.health(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "health":
        print(json.dumps(AgentService(settings).health(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "ask":
        answer = AgentService(settings, mode=args.mode).ask(" ".join(args.question), args.conversation_id)
        print(answer.answer_markdown)
        print("\n## 数据来源")
        for source in answer.sources:
            print(f"- {source.path} ({source.scope})")
        print(f"\ntrace_id: {answer.trace_id}")
        return 0
    if args.command == "eval":
        dataset = settings.project_root / "eval" / "datasets" / args.dataset
        baseline_name = args.baseline or f"{dataset.stem}.{args.mode}.json"
        baseline = settings.project_root / "eval" / "baselines" / baseline_name
        service = AgentService(settings, mode=args.mode)
        report = AgentEvaluator(
            service,
            dataset,
            judge=service.answer_judge,
        ).run(
            baseline_path=baseline,
            update_baseline=args.update_baseline,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        retrieval_ready = not report.get("retrieval") or report["retrieval"].get(
            "passed_threshold", False
        )
        return (
            0
            if report["pass_rate"] == 1.0
            and not report["regressions"]
            and retrieval_ready
            else 1
        )
    if args.command == "serve":
        import uvicorn
        uvicorn.run("data_insight.api:app", host=args.host, port=args.port, reload=False)
        return 0
    if args.command == "ui":
        return _run_ui(settings, args.host, args.port)
    if args.command == "mcp":
        from data_insight.mcp_server import create_mcp_server

        server = create_mcp_server(host=args.host, port=args.port)
        server.run(transport=args.transport)
        return 0
    if args.command == "llm":
        env_path = settings.project_root / ".env"
        if args.llm_command == "init":
            if env_path.exists():
                print(f"Local config already exists: {env_path}")
                print("It was not overwritten.")
                return 0
            shutil.copyfile(settings.project_root / ".env.example", env_path)
            print(f"Created local config: {env_path}")
            print("Open this file locally and replace the three YOUR_* placeholders.")
            print("Do not paste the API key into chat, source code, screenshots, or Git.")
            return 0
        config = AzureLLMConfig.load(settings.project_root)
        if args.llm_command == "login":
            try:
                result = AzureLLMGateway.login(config)
            except Exception as error:
                print(
                    f"LOGIN ERROR: {type(error).__name__}: {error}",
                    file=sys.stderr,
                )
                return 3
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.llm_command == "status":
            print(json.dumps(config.safe_status(), ensure_ascii=False, indent=2))
            return 0 if config.configured else 2
        if args.llm_command == "test":
            try:
                result = AgentService(settings, mode="offline").test_llm_connection()
            except LLMConfigurationError as error:
                print(f"CONFIG ERROR: {error}", file=sys.stderr)
                print("Run `data-agent llm init`, edit .env locally, then retry.", file=sys.stderr)
                return 2
            except Exception as error:
                print(f"CONNECTION ERROR: {type(error).__name__}: {error}", file=sys.stderr)
                return 3
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    return 2


def _run_ui(settings: Settings, host: str, port: int) -> int:
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(settings.project_root / "app.py"),
        "--server.address",
        host,
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--server.fileWatcherType",
        "none",
    ]
    try:
        return subprocess.call(command, cwd=settings.project_root)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
