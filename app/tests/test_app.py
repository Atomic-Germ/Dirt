import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def make_fake_client():
    class FakeClient:
        def __init__(self):
            self.servers = {
                'test-server': {
                    'name': 'test-server',
                    'command': 'echo',
                }
            }
            self.active_processes = {}

        def list_servers(self):
            return list(self.servers.keys())

        def list_active_servers(self):
            return list(self.active_processes.keys())

        def call_tool(self, server_name, tool_name, arguments=None):
            return {"called": f"{server_name}:{tool_name}", "args": arguments or {}}

        def get_server_config(self, name):
            cfg = self.servers.get(name)
            if cfg:
                return SimpleNamespace(name=cfg['name'], command=cfg['command'], args=[], env={}, node_modules_path=None, tools=[])
            return None

        def shutdown(self):
            pass

        def refresh_tools(self):
            pass

    return FakeClient()


@pytest.fixture(autouse=True)
def app_main(monkeypatch, tmp_path):
    # Import the main module and replace its MCP client initializer with a stub
    import app.main as main

    fake = make_fake_client()
    # Ensure the app's mcp_client module uses our fake client during lifespan startup
    import app.mcp_client as mcp_mod
    monkeypatch.setattr(mcp_mod, '_mcp_client', fake)
    monkeypatch.setattr(mcp_mod, 'initialize_mcp_client', lambda config_path=None, autostart=True: fake)
    # Use a temp memory file to avoid touching the repo
    monkeypatch.setattr(main, 'MEMORY_FILE', str(tmp_path / 'memory.json'))
    return main


def test_history_and_clear(app_main):
    main = app_main
    client = TestClient(main.app)

    r = client.get('/history')
    assert r.status_code == 200
    assert r.json() == []

    r = client.post('/clear')
    assert r.status_code == 200
    assert r.json() == {'status': 'cleared'}

    r = client.get('/history')
    assert r.status_code == 200
    assert r.json() == []


def test_mcp_servers_list(app_main):
    main = app_main
    client = TestClient(main.app)

    r = client.get('/mcp/servers')
    assert r.status_code == 200
    data = r.json()
    assert 'configured' in data and 'test-server' in data['configured']


def test_call_mcp_tool(app_main):
    main = app_main
    client = TestClient(main.app)

    payload = {'server_name': 'test-server', 'tool_name': 'echo', 'arguments': {'x': 1}}
    r = client.post('/mcp/tools/call', json=payload)
    assert r.status_code == 200
    assert r.json().get('called') == 'test-server:echo'


def test_weave_dream(app_main):
    main = app_main
    client = TestClient(main.app)

    payload = {'path': './notes', 'pattern': '**/*.md', 'length': 5, 'seed': 'rose'}
    r = client.post('/dream', json=payload)
    assert r.status_code == 200
    # Fake client returns a simple 'called' marker
    assert r.json().get('called') == 'dream-weaver:weave_dream'
