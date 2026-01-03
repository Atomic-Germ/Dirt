#!/usr/bin/env python3
"""
Test script for the Pluggable MCP Client System

This script tests the basic functionality of the MCP client without requiring
actual MCP servers to be installed.
"""

import os
import sys
import json
import tempfile
import shutil
import time
import textwrap
from app.mcp_client import MCPClient, initialize_mcp_client

def test_environment_variable_config():
    """Test loading configuration from environment variables."""
    print("Testing environment variable configuration...")

    # Create a fresh client instance for this test
    client = MCPClient()

    # Set up test environment variables
    test_servers = "test-server1,test-server2"
    os.environ['MCP_SERVERS'] = test_servers

    os.environ['MCP_SERVER_TEST_SERVER1_COMMAND'] = 'echo'
    os.environ['MCP_SERVER_TEST_SERVER1_ARGS'] = '["hello", "world"]'
    os.environ['MCP_SERVER_TEST_SERVER1_ENV'] = '{"TEST_VAR": "test_value"}'

    os.environ['MCP_SERVER_TEST_SERVER2_COMMAND'] = 'cat'
    os.environ['MCP_SERVER_TEST_SERVER2_ARGS'] = '[]'
    os.environ['MCP_SERVER_TEST_SERVER2_ENV'] = '{}'

    try:
        # Load servers from environment
        client.load_servers_from_env()

        # Check that servers were loaded
        servers = client.list_servers()
        assert 'test-server1' in servers, f"test-server1 not loaded. Available: {servers}"
        assert 'test-server2' in servers, f"test-server2 not loaded. Available: {servers}"

        # Check configuration
        config1 = client.get_server_config('test-server1')
        assert config1.command == 'echo', f"Wrong command: {config1.command}"
        assert config1.args == ['hello', 'world'], f"Wrong args: {config1.args}"
        assert config1.env['TEST_VAR'] == 'test_value', f"Wrong env: {config1.env}"

        config2 = client.get_server_config('test-server2')
        assert config2.command == 'cat', f"Wrong command: {config2.command}"
        assert config2.args == [], f"Wrong args: {config2.args}"

        print("✓ Environment variable configuration test passed")

    finally:
        # Clean up environment variables
        for key in list(os.environ.keys()):
            if key.startswith('MCP_SERVER_') or key == 'MCP_SERVERS':
                del os.environ[key]

def test_json_config():
    """Test loading configuration from JSON file."""
    print("Testing JSON configuration...")

    # Create temporary JSON config
    config_data = {
        "servers": {
            "json-server1": {
                "command": "ls",
                "args": ["-la"],
                "env": {"TEST_JSON_VAR": "json_value"},
                "node_modules_path": "/tmp/node_modules"
            },
            "json-server2": {
                "command": "pwd",
                "args": [],
                "env": {},
                "node_modules_path": None
            }
        }
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(config_data, f)
        config_path = f.name

    try:
        # Initialize client with JSON config
        client = initialize_mcp_client(config_path)

        # Check that servers were loaded
        servers = client.list_servers()
        assert 'json-server1' in servers, "json-server1 not loaded"
        assert 'json-server2' in servers, "json-server2 not loaded"

        # Check configuration
        config1 = client.get_server_config('json-server1')
        assert config1.command == 'ls', f"Wrong command: {config1.command}"
        assert config1.args == ['-la'], f"Wrong args: {config1.args}"
        assert config1.env['TEST_JSON_VAR'] == 'json_value', f"Wrong env: {config1.env}"
        assert config1.node_modules_path == '/tmp/node_modules', f"Wrong node_modules: {config1.node_modules_path}"

        config2 = client.get_server_config('json-server2')
        assert config2.command == 'pwd', f"Wrong command: {config2.command}"
        assert config2.node_modules_path is None, f"Wrong node_modules: {config2.node_modules_path}"

        print("✓ JSON configuration test passed")

    finally:
        # Clean up
        os.unlink(config_path)

def test_server_management():
    """Test server start/stop functionality (with mock servers)."""
    print("Testing server management...")

    # Create a client and add a mock server configuration
    client = MCPClient()

    # Add a mock server that just runs 'echo' (should work on any system)
    from app.mcp_client import MCPServerConfig
    mock_config = MCPServerConfig(
        name="mock-server",
        command="echo",
        args=["MCP server running"],
        env={}
    )
    client.servers["mock-server"] = mock_config

    # Test starting server
    success = client.start_server("mock-server")
    assert success, "Failed to start mock server"

    # Check that it's in active servers
    active = client.list_active_servers()
    assert "mock-server" in active, "Mock server not in active list"

    # Test stopping server
    success = client.stop_server("mock-server")
    assert success, "Failed to stop mock server"

    # Check that it's no longer active
    active = client.list_active_servers()
    assert "mock-server" not in active, "Mock server still in active list"

    print("✓ Server management test passed")

def test_message_sending():
    """Test sending messages to servers."""
    print("Testing message sending...")

    # Create a client with a mock server
    client = MCPClient()

    from app.mcp_client import MCPServerConfig
    mock_config = MCPServerConfig(
        name="message-test-server",
        command="cat",  # cat will echo back what we send
        args=[],
        env={}
    )
    client.servers["message-test-server"] = mock_config

    # Start the server
    success = client.start_server("message-test-server")
    assert success, "Failed to start message test server"

    # Send a test message
    test_message = {"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}
    success = client.send_message("message-test-server", test_message)
    assert success, "Failed to send message"

    # Stop the server
    client.stop_server("message-test-server")

    print("✓ Message sending test passed")

def test_tool_calling():
    """Test tool calling functionality."""
    print("Testing tool calling...")

    # Create a client
    client = MCPClient()

    # Test calling a tool on a non-existent server (should fail gracefully)
    result = client.call_tool("non-existent", "test-tool")
    assert result is None, "Tool call on non-existent server should return None"

    # Test with a tiny echo server that returns JSON-RPC result
    from app.mcp_client import MCPServerConfig
    echo_script = "\n".join([
        "import sys, json",
        "",
        "for line in sys.stdin:",
        "    line = line.strip()",
        "    if not line:",
        "        continue",
        "    try:",
        "        msg = json.loads(line)",
        "        resp = {\"jsonrpc\": \"2.0\", \"id\": msg.get(\"id\"), \"result\": {\"echo\": msg}}",
        "    except Exception as e:",
        "        resp = {\"jsonrpc\": \"2.0\", \"id\": None, \"error\": str(e)}",
        "    sys.stdout.write(json.dumps(resp) + \"\\n\")",
        "    sys.stdout.flush()",
        "",
    ])

    mock_config = MCPServerConfig(
        name="tool-test-server",
        command=sys.executable,
        args=["-u", "-c", echo_script],
        env={}
    )
    client.servers["tool-test-server"] = mock_config

    # Start server
    client.start_server("tool-test-server")

    # Call tool and expect an echoed result
    result = client.call_tool("tool-test-server", "test-tool", {"arg1": "value1"})
    assert result is not None, "Tool call should return a result"
    assert result.get("echo", {}).get("method") == "tools/call", f"Unexpected result payload: {result}"

    # Stop server
    client.stop_server("tool-test-server")

    print("✓ Tool calling test passed")


def test_default_config_lookup():
    """Ensure default config search uses home then local overrides before env."""
    print("Testing default config lookup order...")

    original_home = os.environ.get("HOME")
    original_cwd = os.getcwd()

    # Clear any existing MCP-related env to keep the test isolated
    for key in list(os.environ.keys()):
        if key.startswith('MCP_SERVER_') or key == 'MCP_SERVERS':
            del os.environ[key]

    with tempfile.TemporaryDirectory() as tmpdir:
        home_dir = os.path.join(tmpdir, "home")
        workspace_dir = os.path.join(tmpdir, "workspace")
        os.makedirs(os.path.join(home_dir, ".mcp-group"), exist_ok=True)
        os.makedirs(workspace_dir, exist_ok=True)

        home_config_path = os.path.join(home_dir, ".mcp-group", "mcp_config.json")
        workspace_config_path = os.path.join(workspace_dir, "mcp_config.json")

        # Home config defines a server that will be overridden locally
        with open(home_config_path, "w") as f:
            json.dump({
                "servers": {
                    "shared-server": {
                        "command": "echo",
                        "args": ["from-home"],
                        "env": {"HOME_ONLY": "1"}
                    }
                }
            }, f)

        # Local config overrides the shared server and adds a new one
        with open(workspace_config_path, "w") as f:
            json.dump({
                "servers": {
                    "shared-server": {
                        "command": "printf",
                        "args": ["from-local"],
                        "env": {"LOCAL_OVERRIDE": "1"}
                    },
                    "local-only": {
                        "command": "pwd",
                        "args": [],
                        "env": {}
                    }
                }
            }, f)

        try:
            os.environ['HOME'] = home_dir
            os.chdir(workspace_dir)

            client = initialize_mcp_client()

            servers = client.list_servers()
            assert "shared-server" in servers, "Shared server not loaded"
            assert "local-only" in servers, "Local server not loaded"

            shared_config = client.get_server_config("shared-server")
            assert shared_config.command == "printf", f"Local override not applied: {shared_config.command}"
            assert shared_config.env.get("LOCAL_OVERRIDE") == "1", "Local env override missing"

            local_only_config = client.get_server_config("local-only")
            assert local_only_config.command == "pwd", "Local-only server not loaded"

            print("✓ Default config lookup order test passed")
        finally:
            os.chdir(original_cwd)
            if original_home is not None:
                os.environ['HOME'] = original_home
            elif 'HOME' in os.environ:
                del os.environ['HOME']


def test_project_mcp_config_loads_real_servers():
    """Ensure the repo mcp_config.json loads expected MCP servers without starting them."""
    print("Testing project mcp_config.json loading...")

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    config_path = os.path.join(repo_root, "mcp_config.json")

    if not os.path.exists(config_path):
        print("mcp_config.json not found; skipping")
        return

    client = initialize_mcp_client(config_path)

    servers = set(client.list_servers())
    expected = {
        "ollama-consult",
        "dream-weaver",
        "creative-meditate",
        "resonance-engine",
        "mcp-bridge",
    }

    missing = expected - servers
    assert not missing, f"Missing expected servers from mcp_config.json: {missing} (found: {servers})"

    print("✓ Project mcp_config.json load test passed")


def test_real_servers_can_start_if_binaries_present():
    """Start/stop repo-configured servers when their binaries are available in PATH."""
    print("Testing real MCP servers startup...")

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    config_path = os.path.join(repo_root, "mcp_config.json")

    if not os.path.exists(config_path):
        print("mcp_config.json not found; skipping")
        return

    client = initialize_mcp_client(config_path)

    expected = [
        "ollama-consult",
        "dream-weaver",
        "creative-meditate",
        "resonance-engine",
        "mcp-bridge",
    ]

    # Skip if any required binary is missing
    missing_cmds = []
    for name in expected:
        cfg = client.get_server_config(name)
        if cfg is None or not shutil.which(cfg.command):
            missing_cmds.append(name)
    if missing_cmds:
        print(f"Skipping real server start; missing binaries for: {missing_cmds}")
        return

    results = client.start_all_servers()
    failures = [name for name in expected if not results.get(name)]
    assert not failures, f"Failed to start servers: {failures}"

    active = set(client.list_active_servers())
    missing_active = [name for name in expected if name not in active]
    assert not missing_active, f"Expected servers not active: {missing_active}"

    # Allow brief warmup before discovery
    time.sleep(1.0)

    discovered = {}
    for name in expected:
        tools = client.list_tools(name, timeout=5.0) or []
        discovered[name] = tools
        assert tools, f"No tools discovered for {name}. Ensure server implements tools/list or config.tools is set."

    stop_results = client.stop_all_servers()
    stop_failures = [name for name, ok in stop_results.items() if not ok]
    assert not stop_failures, f"Failed to stop servers: {stop_failures}"

    print("Discovered tools (config or heuristic):")
    for name, tools in discovered.items():
        print(f"  - {name}: {tools if tools else 'none detected'}")

    print("✓ Real MCP servers start/stop + discovery test passed")

def run_all_tests():
    """Run all tests."""
    print("Running MCP Client System Tests")
    print("=" * 40)

    tests = [
        test_environment_variable_config,
        test_json_config,
        test_server_management,
        test_message_sending,
        test_tool_calling,
        test_default_config_lookup,
        test_project_mcp_config_loads_real_servers,
        test_real_servers_can_start_if_binaries_present,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} failed: {e}")
            failed += 1
        print()

    print("=" * 40)
    print(f"Tests completed: {passed} passed, {failed} failed")

    if failed > 0:
        print("Some tests failed!")
        sys.exit(1)
    else:
        print("All tests passed! ✓")

if __name__ == "__main__":
    run_all_tests()