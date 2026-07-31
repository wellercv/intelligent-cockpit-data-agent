import sys

from data_insight import cli


def test_ui_ctrl_c_exits_cleanly(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["data-agent", "ui"])

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.subprocess, "call", interrupted)
    assert cli.main() == 0


def test_demo_ctrl_c_exits_cleanly(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["data-agent", "demo"])
    monkeypatch.setenv("DATA_AGENT_DEMO_ROOT", str(tmp_path / "demo"))

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.subprocess, "call", interrupted)
    assert cli.main() == 0