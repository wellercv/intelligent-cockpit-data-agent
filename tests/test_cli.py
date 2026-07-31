import sys

from data_insight import cli


def test_ui_ctrl_c_exits_cleanly(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["data-agent", "ui"])

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.subprocess, "call", interrupted)
    assert cli.main() == 0