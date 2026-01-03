import json
import types
import sys
from types import SimpleNamespace

import pytest

from app.ollama_adapter import OllamaAdapter


def test_cli_fallback(monkeypatch):
    # Simulate ollama CLI returning JSON on stdout
    def fake_run(cmd, input, capture_output, text, check):
        return SimpleNamespace(stdout='{"result": {"ok": true}}', returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)

    adapter = OllamaAdapter(model="erukude/multiagent-orchestrator:3b")
    res = adapter.call("test_tool", {"x": 1})
    assert isinstance(res, dict)
    assert res.get("result", {}).get("ok") is True


def test_python_package_path(monkeypatch):
    # Create a fake ollama module with a generate function
    fake_module = types.SimpleNamespace()

    def fake_generate(model, prompt=None):
        class R:
            text = '{"from": "py", "ok": true}'

        return R()

    fake_module.generate = fake_generate

    monkeypatch.setitem(sys.modules, "ollama", fake_module)

    adapter = OllamaAdapter(model="dummy")
    res = adapter.call("tool", {"a": 2})
    assert isinstance(res, dict)
    assert res.get("from") == "py"
