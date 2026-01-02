# Dirt: Pluggable MCP Chat Client

A FastAPI-based MCP client that auto-starts configured stdio MCP servers, discovers their tools, and exposes them to chat (via Ollama) and REST/CLI. Ships with example servers defined in `mcp_config.json` (ollama-consult, dream-weaver, creative-meditate, resonance-engine, mcp-bridge).

## Features

- **Auto-start + discovery**: Start MCP servers from env/JSON config, then call `tools/list` to learn exact tool names.
- **Chat tool-calls**: Models can call MCP tools through the `call_mcp_tool` schema; tool names are injected into the system prompt.
- **REST + CLI**: Manage servers and call tools via FastAPI endpoints or `mcp_cli.py`.
- **Config flexibility**: Load from `mcp_config.json` or environment variables; supports stdio servers with custom env/args.
- **Tested flow**: System tests start real servers, discover tools, and exercise tool calls end-to-end.

## Architecture

1. **MCPClient** (`app/mcp_client.py`): Manages stdio MCP servers, JSON-RPC, `tools/list`, and tool calls.
2. **Web API** (`app/main.py`): FastAPI endpoints for chat, server lifecycle, and tool calls; injects discovered tool names into chat prompt.
3. **CLI** (`app/mcp_cli.py`): Convenience wrapper for listing/starting/stopping servers and calling tools.

## Installation

### Prerequisites

- Python 3.12+
- Installed MCP servers accessible on PATH (the repo includes a sample `mcp_config.json` pointing to stdio servers like `dream-weaver` and `creative-meditate`).

### Install Python dependencies

```bash
pip install -e .
```

## Configuration

### Method 1: Environment variables

Set environment variables to configure MCP servers:

```bash
# List of servers to load
export MCP_SERVERS="ollama-consult,dream-weaver,creative-meditate"

# Configuration for each server
export MCP_SERVER_OLLAMA_CONSULT_COMMAND="mcp-ollama-consult"
export MCP_SERVER_OLLAMA_CONSULT_ARGS="[]"
export MCP_SERVER_OLLAMA_CONSULT_ENV='{"MEMORY_DIR": "/path/to/memory", "MCP_AUTO_MODEL_SETTINGS": "1"}'

export MCP_SERVER_DREAM_WEAVER_COMMAND="dream-weaver"
export MCP_SERVER_DREAM_WEAVER_ARGS="[]"
export MCP_SERVER_DREAM_WEAVER_ENV='{"MEMORY_DIR": "/path/to/memory"}'

# Optional: Specify node_modules path for local installations
export MCP_SERVER_MY_SERVER_NODE_MODULES="/path/to/project/node_modules"
```

### Method 2: JSON configuration file

Create a `mcp_config.json` file:

```json
{
  "servers": {
    "ollama-consult": {
      "command": "mcp-ollama-consult",
      "args": [],
      "env": {
        "MEMORY_DIR": "/home/user/.mcp-group/",
        "KNOWLEDGE_BASE_PATH": "/home/user/.mcp-group/",
        "MCP_AUTO_MODEL_SETTINGS": "1"
      },
      "node_modules_path": null
    },
    "dream-weaver": {
      "command": "dream-weaver",
      "args": [],
      "env": {
        "MEMORY_DIR": "/home/user/.mcp-group/",
        "KNOWLEDGE_BASE_PATH": "/home/user/.mcp-group/"
      },
      "node_modules_path": null
    }
  }
}
```

## Usage

### Run the API + chat

```bash
python app/main.py
```

- Chat endpoint: `POST /chat` (uses Ollama; models can return `call_mcp_tool` actions and the server will execute them).
- MCP management: `GET /mcp/servers`, `POST /mcp/servers/start`, `POST /mcp/servers/stop`, `POST /mcp/tools/call`, `GET /mcp/servers/{server_name}/config`.

Example:

```bash
# List servers
curl http://localhost:8000/mcp/servers

# Start a server
curl -X POST http://localhost:8000/mcp/servers/start \
  -H "Content-Type: application/json" \
  -d '{"server_name": "ollama-consult"}'

# Call a tool
curl -X POST http://localhost:8000/mcp/tools/call \
  -H "Content-Type: application/json" \
  -d '{
    "server_name": "ollama-consult",
    "tool_name": "consult_ollama",
    "arguments": {"prompt": "Hello, world!"}
  }'
```

### CLI tool

```bash
# List configured servers
python app/mcp_cli.py list

# Start a server
python app/mcp_cli.py start ollama-consult

# Call a tool
python app/mcp_cli.py call ollama-consult consult_ollama --args '{"prompt": "Hello, world!"}'
```

### Python API

Use the MCP client directly in Python code:

```python
from mcp_client import initialize_mcp_client

# Initialize with environment variables
client = initialize_mcp_client()

# Or initialize with JSON config
client = initialize_mcp_client("mcp_config.json")

# Start a server
client.start_server("ollama-consult")

# Call a tool
result = client.call_tool("ollama-consult", "consult_ollama", {
    "prompt": "Hello, world!",
    "model": "kimi-k2-thinking:cloud"
})

# Stop a server
client.stop_server("ollama-consult")
```

## MCP server compatibility

Works with stdio MCP servers that follow the protocol and support `tools/list`. Servers are started as subprocesses with optional env/args.

## Environment variables reference

### Global configuration

- `MCP_SERVERS`: Comma-separated list of server names to load

### Per-server configuration

For each server named `SERVER_NAME`:

- `MCP_SERVER_{SERVER_NAME}_COMMAND`: Executable command to run the server
- `MCP_SERVER_{SERVER_NAME}_ARGS`: JSON array of command-line arguments
- `MCP_SERVER_{SERVER_NAME}_ENV`: JSON object of environment variables for the server
- `MCP_SERVER_{SERVER_NAME}_NODE_MODULES`: Path to node_modules directory (optional)

### Example environment setup

```bash
# Global config
export MCP_SERVERS="ollama-consult,dream-weaver,resonance-engine"

# Ollama Consult server
export MCP_SERVER_OLLAMA_CONSULT_COMMAND="mcp-ollama-consult"
export MCP_SERVER_OLLAMA_CONSULT_ARGS="[]"
export MCP_SERVER_OLLAMA_CONSULT_ENV='{"MEMORY_DIR": "/tmp/mcp", "MCP_AUTO_MODEL_SETTINGS": "1"}'

# Dream Weaver server
export MCP_SERVER_DREAM_WEAVER_COMMAND="dream-weaver"
export MCP_SERVER_DREAM_WEAVER_ARGS="[]"
export MCP_SERVER_DREAM_WEAVER_ENV='{"MEMORY_DIR": "/tmp/mcp"}'

# Resonance Engine server
export MCP_SERVER_RESONANCE_ENGINE_COMMAND="resonance-engine"
export MCP_SERVER_RESONANCE_ENGINE_ARGS="[]"
export MCP_SERVER_RESONANCE_ENGINE_ENV='{"MEMORY_DIR": "/tmp/mcp"}'
```

## Development

### Adding new MCP servers

1. Install the Node.js package: `npm install -g your-mcp-server`
2. Add configuration via environment variables or JSON config
3. The system will automatically discover and load the server

### Extending the client

The `MCPClient` can be extended for other transports or health monitoring; tool discovery currently relies on `tools/list`.

## Troubleshooting

### Server Won't Start

1. Check that the Node.js package is installed: `npm list -g your-server`
2. Verify the command is correct: `which your-server-command`
3. Check environment variables are set correctly
4. Review server logs in the console output

### Tool Calls Fail

1. Ensure the server is started: `python mcp_cli.py list`
2. Verify the tool name is correct
3. Check the arguments format matches the tool's requirements
4. Review MCP protocol compliance

### Permission Issues

1. Ensure the user has execute permissions on Node.js binaries
2. Check file system permissions for configured paths
3. Verify the server can write to configured directories

## License

This project is open source. See individual MCP server licenses for their terms.