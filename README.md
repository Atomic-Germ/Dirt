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

### Install from source (editable or wheel)

```bash
# Editable dev install
pip install -e .

# Or build + install a wheel
pip install build
python -m build
pip install dist/dirt-0.2.0-py3-none-any.whl
```

This installs two entry points:
- `dirt-api` — runs the FastAPI server (wraps `uvicorn app.main:app`).
- `dirt-mcp` — CLI for listing/starting/stopping servers and calling tools.

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
dirt-api
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
dirt-mcp list

# Start a server
dirt-mcp start ollama-consult

# Call a tool
dirt-mcp call ollama-consult consult_ollama --args '{"prompt": "Hello, world!"}'
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

---

## __Project Purpose & Architecture__

__Dirt__ is a FastAPI-based MCP (Model Context Protocol) client that serves as a "contemplative AI interface" called "The Bridge". It provides:

1. __Multi-modal AI Chat Interface__: Web-based chat with Ollama models that can call MCP server tools
2. __MCP Server Management__: Auto-starts and manages stdio-based MCP servers (ollama-consult, dream-weaver, creative-meditate, resonance-engine, mcp-bridge)
3. __Tool Calling System__: Models can invoke MCP tools through a `call_mcp_tool` schema
4. __Memory & Heritage System__: Persistent conversation memory and "heritage context" for long-term knowledge retention
5. __REST/CLI APIs__: Full programmatic access to MCP server lifecycle and tool calls

## __Code Structure Map__

📁 /home/atomic-germ/Documents/Code/Dirt/
├── 📄 pyproject.toml          # Python project config (FastAPI + Ollama deps)
├── 📄 README.md               # Comprehensive documentation
├── 📄 mcp_config.json         # MCP server configurations (5 servers)
├── 📄 heritage_context.json   # Long-term knowledge artifacts (currently empty)
├── 📄 bridge_memory.json      # Chat conversation history
└── 📁 app/
    ├── 📄 __init__.py
    ├── 📄 main.py             # FastAPI app with chat, MCP management, streaming
    ├── 📄 mcp_client.py       # Core MCP client (stdio subprocess management)
    ├── 📄 mcp_cli.py          # Command-line interface for MCP operations
    ├── 📄 test_mcp_client.py  # Unit tests
    └── 📁 static/             # Web interface
        ├── 📄 index.html      # Chat UI ("The Bridge" interface)
        ├── 📄 script.js       # Frontend logic (streaming, tag processing)
        └── 📄 style.css       # Dark theme with think-block collapsible sections

## __Key Components__

### __Backend (FastAPI)__

- __Chat Endpoint__ (`/chat`): Streaming responses with tool calling, memory persistence, heritage context injection
- __MCP Management__: Start/stop servers, tool discovery, tool invocation
- __Memory System__: Conversation history + heritage artifacts for context
- __Model Integration__: Ollama client with automatic tool capability detection

### __MCP Client System__

- __Server Lifecycle__: Auto-starts configured MCP servers as subprocesses
- __JSON-RPC Communication__: Stdio-based MCP protocol implementation
- __Tool Discovery__: Dynamic tool enumeration from active servers
- __Configuration__: Environment variables + JSON config files

### __Web Interface__

- __Chat UI__: Dark-themed interface with collapsible "think blocks" for AI reasoning
- __Streaming__: Real-time response rendering with tag processing
- __Memory Controls__: Clear history, model selection, streaming toggle


## License

This project is open source. See individual MCP server licenses for their terms.