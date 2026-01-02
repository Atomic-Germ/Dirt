import json
import os
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from ollama import Client
from mcp_client import initialize_mcp_client, get_mcp_client

# Initialize Ollama client with the specified host
# We use 127.0.0.1:11434 as the default to ensure it hits the local Ollama instance
ollama_host = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')
print(f"Connecting to Ollama at: {ollama_host}")
client = Client(host=ollama_host)

app = FastAPI()

# Memory file path
MEMORY_FILE = "bridge_memory.json"

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: str
    messages: List[Message]
    remember: bool = True

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            # If file is corrupted, return empty list
            return []
    return []


def load_heritage_context() -> str:
    """Best-effort load of heritage context; tolerate missing or empty/invalid JSON."""
    h_path = "heritage_context.json"
    if not os.path.exists(h_path):
        return ""

    try:
        with open(h_path, "r") as f:
            raw = f.read().strip()
            if not raw:
                return ""
            h_data = json.loads(raw)
            return "\n".join([item.get("content", "") for item in h_data if isinstance(item, dict)])
    except (json.JSONDecodeError, ValueError):
        return ""


def mcp_tools_schema() -> List[Dict[str, Any]]:
    """Define a generic tool callable by the model to reach any MCP server/tool."""
    return [
        {
            "type": "function",
            "function": {
                "name": "call_mcp_tool",
                "description": "Call a tool exposed by an MCP server (stdio).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "server_name": {
                            "type": "string",
                            "description": "Name of the MCP server to target (see configured list)."
                        },
                        "tool_name": {
                            "type": "string",
                            "description": "Exact tool name to call on that server."
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Arguments for the tool (if any).",
                            "additionalProperties": True
                        }
                    },
                    "required": ["server_name", "tool_name"],
                    "additionalProperties": False
                }
            }
        }
    ]

def save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)

@app.post("/chat")
async def chat(request: ChatRequest):
    try:
        # Load existing memory to provide context if 'remember' is true
        memory = load_memory()

        # Load heritage context (best effort)
        heritage_context = load_heritage_context()

        # Describe available MCP servers for tool-capable models
        mcp_client = get_mcp_client()
        server_list = mcp_client.list_servers()
        servers_text = ", ".join(server_list) if server_list else "mcp-bridge (default)"
        server_tool_descriptions = []
        for name in server_list:
            cfg = mcp_client.get_server_config(name)
            if cfg and cfg.tools:
                server_tool_descriptions.append(f"{name}: {', '.join(cfg.tools)}")
            else:
                server_tool_descriptions.append(f"{name}: tools unknown (inspect server docs)")
        servers_tools_text = "; ".join(server_tool_descriptions)

        # Prepare messages for Ollama
        system_prompt = (
            "You are the voice of The Bridge, a contemplative AI interface for the 'Dirt' project. "
            f"{heritage_context} "
            "Your goal is to help the user weave their thoughts and remember their insights. "
            "Be concise, atmospheric, and helpful. That, or be silly, sexy, and horny. "
            "Tooling: If you are tool-capable, you can call MCP tools on the configured servers "
            f"({servers_text}). When the user says \"Use the Bridge\" or asks to log/remember sessions, "
            "first call tool mcp-bridge_bridge_start_session on server mcp-bridge, then use other mcp-bridge_* "
            "tools as needed. If you cannot call tools, continue with a helpful text response. "
            f"Known tools (from config or discovery): {servers_tools_text}. "
            "Common tools: mcp-bridge: bridge_start_session, bridge_log_meditation, bridge_log_consult; "
            "creative-meditate: creative_meditate, creative_insight, creative_ponder; "
            "dream-weaver: weave_dream; ollama-consult: consult_ollama, list_ollama_models; "
            "resonance-engine: observe_ecosystem_state, detect_emergent_patterns. "
            "Use the tool call schema named call_mcp_tool with fields server_name, tool_name, arguments."
        )
        
        context_messages = [{"role": "system", "content": system_prompt}]
        if request.remember:
            # Take last 10 messages from memory for context
            context_messages += memory[-10:]
        
        # Combine context with current request messages
        full_messages = context_messages + [m.model_dump() for m in request.messages]

        tools = mcp_tools_schema()

        print(f"Sending tool-capable request to Ollama model: {request.model} at {client._client.base_url}")

        # Tool calling loop (non-stream) so we can execute MCP calls, then stream final text once ready
        max_tool_hops = 4
        hop = 0
        last_response = None

        while hop < max_tool_hops:
            hop += 1
            last_response = client.chat(model=request.model, messages=full_messages, tools=tools)
            message = last_response.get("message", {})
            tool_calls = message.get("tool_calls", []) or []

            # If no tool calls, break and return message content
            if not tool_calls:
                break

            # Execute each tool call and append tool results to the conversation
            for tool_call in tool_calls:
                fn = tool_call.get("function", {})
                name = fn.get("name")
                arguments_raw = fn.get("arguments") or "{}"
                if isinstance(arguments_raw, dict):
                    arguments = arguments_raw
                else:
                    try:
                        arguments = json.loads(arguments_raw)
                    except (json.JSONDecodeError, TypeError):
                        arguments = {}

                if name != "call_mcp_tool":
                    tool_result = {"error": f"Unknown tool {name}"}
                else:
                    server_name = arguments.get("server_name")
                    tool_name = arguments.get("tool_name")
                    tool_args = arguments.get("arguments", {})
                    tool_result = mcp_client.call_tool(server_name, tool_name, tool_args)
                    if tool_result is None:
                        tool_result = {"error": f"Tool call failed for {server_name}:{tool_name}"}

                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "content": json.dumps(tool_result)
                }
                full_messages.append(tool_message)

        # Prepare final assistant content
        final_content = ""
        if last_response:
            final_content = last_response.get("message", {}).get("content", "")

        # Update memory after response is ready
        if request.remember:
            assistant_message = {"role": "assistant", "content": final_content}
            memory.append(request.messages[-1].model_dump())
            memory.append(assistant_message)
            save_memory(memory)

        # Stream the final content as a single chunk for frontend compatibility
        def generate():
            yield final_content

        return StreamingResponse(generate(), media_type="text/plain")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

class SeedRequest(BaseModel):
    content: str
    tags: List[str] = []

@app.post("/seed")
async def seed_heritage(request: SeedRequest):
    try:
        # In a real scenario, we'd call the MCP tool here. 
        # For now, we'll append to our local heritage_context.json
        h_path = "heritage_context.json"
        h_data = []
        if os.path.exists(h_path):
            with open(h_path, "r") as f:
                h_data = json.load(f)
        
        new_artifact = {
            "id": f"artifact-{int(os.times().elapsed * 1000)}",
            "content": request.content,
            "tags": request.tags
        }
        h_data.append(new_artifact)
        
        with open(h_path, "w") as f:
            json.dump(h_data, f, indent=2)
            
        return {"status": "seeded", "id": new_artifact["id"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/history")
async def get_history():
    return load_memory()

@app.post("/clear")
async def clear_history():
    save_memory([])
    return {"status": "cleared"}

@app.get("/models")
async def get_models():
    try:
        models_info = client.list()
        # In newer ollama-python, models have a 'model' attribute for the name
        model_names = [m.model for m in models_info.models]
        print(f"Fetched {len(model_names)} models from Ollama")
        return model_names
    except Exception as e:
        print(f"Failed to fetch models from Ollama: {e}")
        # Fallback to a more robust way if the above fails
        try:
            models_info = client.list()
            model_names = [m['model'] for m in models_info['models']]
            return model_names
        except:
            return ["kimi-k2-thinking:cloud", "lucasmg/gemma3-12b-tool-thinking-true:latest"]

# MCP Server Management Endpoints

class MCPServerAction(BaseModel):
    server_name: str

class MCPToolCall(BaseModel):
    server_name: str
    tool_name: str
    arguments: Optional[Dict[str, Any]] = None

@app.on_event("startup")
async def startup_event():
    """Initialize MCP client on startup."""
    # Initialize MCP client (loads home/local JSON configs then env) and autostart servers
    mcp_client = initialize_mcp_client(autostart=True)
    # After servers start, discover tools so prompt can list the real names
    mcp_client.refresh_tools()

@app.get("/mcp/servers")
async def list_mcp_servers():
    """List all configured MCP servers."""
    mcp_client = get_mcp_client()
    return {
        "configured": mcp_client.list_servers(),
        "active": mcp_client.list_active_servers()
    }

@app.post("/mcp/servers/start")
async def start_mcp_server(request: MCPServerAction):
    """Start an MCP server."""
    mcp_client = get_mcp_client()
    success = mcp_client.start_server(request.server_name)
    return {"success": success, "server": request.server_name}


@app.post("/mcp/servers/start_all")
async def start_all_mcp_servers():
    """Start all configured MCP servers."""
    mcp_client = get_mcp_client()
    results = mcp_client.start_all_servers()
    return {"results": results}

@app.post("/mcp/servers/stop")
async def stop_mcp_server(request: MCPServerAction):
    """Stop an MCP server."""
    mcp_client = get_mcp_client()
    success = mcp_client.stop_server(request.server_name)
    return {"success": success, "server": request.server_name}

@app.post("/mcp/tools/call")
async def call_mcp_tool(request: MCPToolCall):
    """Call a tool on an MCP server."""
    mcp_client = get_mcp_client()
    result = mcp_client.call_tool(request.server_name, request.tool_name, request.arguments)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to call MCP tool")
    return result

@app.get("/mcp/servers/{server_name}/config")
async def get_mcp_server_config(server_name: str):
    """Get configuration for a specific MCP server."""
    mcp_client = get_mcp_client()
    config = mcp_client.get_server_config(server_name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Server {server_name} not found")
    return {
        "name": config.name,
        "command": config.command,
        "args": config.args,
        "env": config.env,
        "node_modules_path": config.node_modules_path,
        "tools": config.tools,
    }

# Serve static files
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)