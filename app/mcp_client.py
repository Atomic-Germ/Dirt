"""
Pluggable MCP Client System with Environment Variables and Stdio Compatibility

This module provides a flexible MCP client that can dynamically load Node-based MCP servers,
communicate via stdio, and be configured through environment variables.
"""

import os
import json
import asyncio
import subprocess
import sys
import select
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import threading
import queue
import logging
import itertools  # Confirming presence of itertools import
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""
    name: str
    command: str
    args: List[str] = None
    env: Dict[str, str] = None
    node_modules_path: Optional[str] = None
    tools: Optional[List[str]] = None

    def __post_init__(self):
        if self.args is None:
            self.args = []
        if self.env is None:
            self.env = {}
        if self.tools is None:
            self.tools = []

class MCPClient:
    """
    Pluggable MCP client that manages Node-based MCP servers via stdio.
    """

    def __init__(self):
        self.servers: Dict[str, MCPServerConfig] = {}
        self.active_processes: Dict[str, subprocess.Popen] = {}
        self.executor = ThreadPoolExecutor(max_workers=10)
        self._shutdown_event = threading.Event()
        self.response_queues: Dict[str, queue.Queue] = {}
        self._id_counters: Dict[str, Any] = {}
        self._counter_locks: Dict[str, threading.Lock] = {}

    def load_servers_from_env(self) -> None:
        """
        Load MCP server configurations from environment variables.

        Expected format:
        MCP_SERVERS=config1,config2,config3

        For each server:
        MCP_SERVER_{NAME}_COMMAND=command
        MCP_SERVER_{NAME}_ARGS=args (JSON array)
        MCP_SERVER_{NAME}_ENV=env_vars (JSON object)
        MCP_SERVER_{NAME}_NODE_MODULES=path_to_node_modules
        """
        servers_str = os.environ.get('MCP_SERVERS', '')
        if not servers_str:
            logger.info("No MCP_SERVERS environment variable found")
            return

        server_names = [name.strip() for name in servers_str.split(',')]

        for server_name in server_names:
            self._load_server_config_from_env(server_name)

    def _load_server_config_from_env(self, server_name: str) -> None:
        """Load a single server configuration from environment variables."""
        # Convert server name to uppercase and replace hyphens with underscores for env var compatibility
        env_name = server_name.upper().replace('-', '_')
        prefix = f"MCP_SERVER_{env_name}"

        command = os.environ.get(f"{prefix}_COMMAND")
        if not command:
            logger.warning(f"No command found for server {server_name} (looked for {prefix}_COMMAND)")
            return

        # Parse args as JSON array if present
        args_str = os.environ.get(f"{prefix}_ARGS", "[]")
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON for {prefix}_ARGS: {args_str}")
            args = []

        # Parse env as JSON object if present
        env_str = os.environ.get(f"{prefix}_ENV", "{}")
        try:
            env = json.loads(env_str)
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON for {prefix}_ENV: {env_str}")
            env = {}

        node_modules_path = os.environ.get(f"{prefix}_NODE_MODULES")

        config = MCPServerConfig(
            name=server_name,
            command=command,
            args=args,
            env=env,
            node_modules_path=node_modules_path
        )

        self.servers[server_name] = config
        logger.info(f"Loaded MCP server config: {server_name}")

    def load_servers_from_json(self, config_path: str) -> None:
        """
        Load MCP server configurations from a JSON file.

        Format:
        {
            "servers": {
                "server_name": {
                    "command": "command",
                    "args": ["arg1", "arg2"],
                    "env": {"VAR": "value"},
                    "node_modules_path": "/path/to/node_modules"
                }
            }
        }
        """
        try:
            with open(config_path, 'r') as f:
                config_data = json.load(f)

            servers_data = config_data.get('servers', {})

            for server_name, server_config in servers_data.items():
                config = MCPServerConfig(
                    name=server_name,
                    command=server_config.get('command', ''),
                    args=server_config.get('args', []),
                    env=server_config.get('env', {}),
                    node_modules_path=server_config.get('node_modules_path'),
                    tools=server_config.get('tools', [])
                )

                self.servers[server_name] = config
                logger.info(f"Loaded MCP server config from JSON: {server_name}")

        except FileNotFoundError:
            logger.debug(f"No MCP config found at {config_path}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse MCP config from {config_path}: {e}")

    def start_server(self, server_name: str) -> bool:
        """
        Start an MCP server process.

        Returns True if successful, False otherwise.
        """
        if server_name not in self.servers:
            logger.error(f"Server {server_name} not configured")
            return False

        if server_name in self.active_processes:
            logger.warning(f"Server {server_name} already running")
            return True

        config = self.servers[server_name]

        # Prepare environment variables
        env = os.environ.copy()
        env.update(config.env)

        # Add node_modules to PATH if specified
        if config.node_modules_path:
            node_bin_path = os.path.join(config.node_modules_path, '.bin')
            if 'PATH' in env:
                env['PATH'] = f"{node_bin_path}:{env['PATH']}"
            else:
                env['PATH'] = node_bin_path

        try:
            # Start the process with stdio pipes
            process = subprocess.Popen(
                [config.command] + config.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,  # Line buffered
                universal_newlines=True
            )

            self.active_processes[server_name] = process
            logger.info(f"Started MCP server: {server_name}")

            # Start background threads to handle stdout/stderr
            self.executor.submit(self._handle_server_output, server_name, process.stdout, 'stdout')
            self.executor.submit(self._handle_server_output, server_name, process.stderr, 'stderr')

            # Initialize response queue and id counter for this server
            self.response_queues[server_name] = queue.Queue()
            self._id_counters[server_name] = itertools.count(1)
            self._counter_locks[server_name] = threading.Lock()

            return True

        except Exception as e:
            logger.error(f"Failed to start server {server_name}: {e}")
            return False

    def discover_tools(self, server_name: str, timeout: float = 3.0) -> Optional[List[str]]:
        """Best-effort discovery: read recent stdout lines that look like tool announcements.

        Note: Proper MCP tool discovery requires protocol messages; here we heuristically
        collect any logged tools. If none found, return None.
        """
        # First try config-declared tools
        config = self.servers.get(server_name)
        if config and config.tools:
            return config.tools

        # Attempt protocol tools/list if server is active
        response = self.rpc_request(server_name, "tools/list", {}, timeout=timeout)
        if response and isinstance(response, dict):
            result = response.get("result") or {}
            tools = result.get("tools") if isinstance(result, dict) else None
            if isinstance(tools, list):
                names = []
                for t in tools:
                    if isinstance(t, dict):
                        name = t.get("name") or t.get("tool")
                        if name:
                            names.append(name)
                    elif isinstance(t, str):
                        names.append(t)
                if names:
                    return names
        return None

    def start_all_servers(self) -> Dict[str, bool]:
        """Start all configured servers; return per-server success map."""
        results: Dict[str, bool] = {}
        for name in self.list_servers():
            results[name] = self.start_server(name)
        return results

    def stop_server(self, server_name: str) -> bool:
        """
        Stop an MCP server process.

        Returns True if successful, False otherwise.
        """
        if server_name not in self.active_processes:
            logger.warning(f"Server {server_name} not running")
            return True

        process = self.active_processes[server_name]

        try:
            process.terminate()

            # Wait for up to 5 seconds for graceful shutdown
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning(f"Server {server_name} didn't terminate gracefully, killing...")
                process.kill()
                process.wait()

            del self.active_processes[server_name]
            self.response_queues.pop(server_name, None)
            self._id_counters.pop(server_name, None)
            logger.info(f"Stopped MCP server: {server_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to stop server {server_name}: {e}")
            return False

    def stop_all_servers(self) -> Dict[str, bool]:
        """Stop all active servers; return per-server success map."""
        results: Dict[str, bool] = {}
        for name in list(self.active_processes.keys()):
            results[name] = self.stop_server(name)
        return results

    def _handle_server_output(self, server_name: str, stream, stream_type: str) -> None:
        """Handle stdout/stderr output from a server process with timeout."""
        try:
            while not self._shutdown_event.is_set():
                # Use select to wait for data with timeout
                ready, _, _ = select.select([stream], [], [], 1.0)  # 1 second timeout
                if not ready:
                    continue  # Timeout, check shutdown event

                line = stream.readline()
                if not line:  # EOF
                    break

                # Process the output line
                self._process_server_output(server_name, line.strip(), stream_type)

        except Exception as e:
            logger.error(f"Error handling {stream_type} for server {server_name}: {e}")

    def _process_server_output(self, server_name: str, line: str, stream_type: str) -> None:
        """Process a line of output from a server."""
        if stream_type == 'stderr':
            logger.warning(f"Server {server_name} stderr: {line}")
        else:
            logger.debug(f"Server {server_name} stdout: {line}")

            # Here you would parse MCP protocol messages
            # For now, just log them
            try:
                message = json.loads(line)
                logger.info(f"MCP message from {server_name}: {message}")
                # If this is a response with an id, enqueue it for rpc_request consumers
                if isinstance(message, dict) and 'id' in message and ('result' in message or 'error' in message):
                    q = self.response_queues.get(server_name)
                    if q:
                        q.put(message)
            except json.JSONDecodeError:
                logger.debug(f"Non-JSON output from {server_name}: {line}")

    def send_message(self, server_name: str, message: Dict[str, Any]) -> bool:
        """
        Send a JSON-RPC message to an MCP server.

        Returns True if successful, False otherwise.
        """
        if server_name not in self.active_processes:
            logger.error(f"Server {server_name} not running")
            return False

        process = self.active_processes[server_name]

        try:
            message_json = json.dumps(message) + '\n'
            process.stdin.write(message_json)
            process.stdin.flush()
            logger.debug(f"Sent message to {server_name}: {message}")
            return True

        except Exception as e:
            logger.error(f"Failed to send message to server {server_name}: {e}")
            return False

    def rpc_request(self, server_name: str, method: str, params: Dict[str, Any], timeout: float = 5.0) -> Optional[Dict[str, Any]]:
        """Send a JSON-RPC request and wait for the response."""
        if server_name not in self.active_processes:
            logger.error(f"Server {server_name} not running")
            return None

        q = self.response_queues.get(server_name)
        counter = self._id_counters.get(server_name)
        lock = self._counter_locks.get(server_name)
        if q is None or counter is None or lock is None:
            logger.error(f"No response queue for server {server_name}")
            return None

        with lock:
            req_id = next(counter)
        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        if not self.send_message(server_name, message):
            return None

        try:
            response = q.get(timeout=timeout)
            return response
        except queue.Empty:
            logger.error(f"Timeout waiting for response from server {server_name} for method {method}")
            return None

    def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """
        Call a tool on an MCP server.

        This is a simplified implementation. In a real MCP client,
        you'd need to handle the full JSON-RPC protocol with request/response matching.
        """
        if arguments is None:
            arguments = {}

        response = self.rpc_request(
            server_name,
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments
            }
        )

        if response is None:
            return None

        if 'error' in response:
            return {"error": response['error']}

        return response.get('result', response)

    def list_servers(self) -> List[str]:
        """List all configured server names."""
        return list(self.servers.keys())

    def list_active_servers(self) -> List[str]:
        """List names of currently active servers."""
        return list(self.active_processes.keys())

    def list_tools(self, server_name: str, timeout: float = 3.0) -> Optional[List[str]]:
        """Public wrapper for discover_tools."""
        return self.discover_tools(server_name, timeout=timeout)

    def refresh_tools(self, timeout: float = 3.0) -> Dict[str, List[str]]:
        """Discover tools for all active servers and store them on their configs."""
        discovered: Dict[str, List[str]] = {}
        for name in self.list_active_servers():
            tools = self.list_tools(name, timeout=timeout) or []
            cfg = self.servers.get(name)
            if cfg is not None:
                cfg.tools = tools
            discovered[name] = tools
        return discovered

    def get_server_config(self, server_name: str) -> Optional[MCPServerConfig]:
        """Get the configuration for a specific server."""
        return self.servers.get(server_name)

    def shutdown(self) -> None:
        """Shutdown all servers and cleanup resources."""
        logger.info("Shutting down MCP client...")

        self._shutdown_event.set()

        # Stop all active servers
        for server_name in list(self.active_processes.keys()):
            self.stop_server(server_name)

        self.executor.shutdown(wait=True)
        logger.info("MCP client shutdown complete")

# Global client instance
_mcp_client = None

def get_mcp_client() -> MCPClient:
    """Get the global MCP client instance."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client

def initialize_mcp_client(config_path: Optional[str] = None, autostart: bool = False) -> MCPClient:
    """
    Initialize the MCP client with configuration.

    Loading order (later entries override earlier ones for matching server names):
    1. Home config: ~/.mcp-group/mcp_config.json
    2. Local config: ./mcp_config.json
    3. Explicit config_path if provided (takes precedence over defaults)
    4. Environment variables (final override)
    """
    client = get_mcp_client()

    # Default config search paths
    default_paths = [
        os.path.expanduser("~/.mcp-group/mcp_config.json"),
        os.path.join(os.getcwd(), "mcp_config.json"),
    ]

    # If an explicit config path is provided, use it after defaults so it can override
    if config_path:
        default_paths.append(config_path)

    for path in default_paths:
        client.load_servers_from_json(path)

    # Environment variables have final say
    client.load_servers_from_env()

    if autostart:
        client.start_all_servers()

    return client

# Cleanup on exit
import atexit
atexit.register(lambda: get_mcp_client().shutdown())