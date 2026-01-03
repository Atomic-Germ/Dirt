import json
import logging
import subprocess
import time
import os
import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any
from ollama import Client
from app.heuristics import compress_prompt, suggest_settings, parse_model_name
# ResponseError may be raised by the underlying ollama client when a model runner crashes
try:
    from ollama._types import ResponseError
except Exception:  # pragma: no cover - best-effort import for different ollama sdk shapes
    ResponseError = None

# Robust import handling for both package and direct execution
def _import_mcp_client():
    """Import MCP client with robust path handling."""
    try:
        # Try package import first
        from app.mcp_client import initialize_mcp_client, get_mcp_client
        return initialize_mcp_client, get_mcp_client
    except ImportError:
        try:
            # Try direct import with sys.path adjustment
            script_dir = Path(__file__).parent
            if str(script_dir) not in sys.path:
                sys.path.insert(0, str(script_dir))
            from mcp_client import initialize_mcp_client, get_mcp_client
            return initialize_mcp_client, get_mcp_client
        except ImportError as e:
            raise ImportError(f"Failed to import MCP client: {e}. Ensure MCP client is properly installed or available.")

initialize_mcp_client, get_mcp_client = _import_mcp_client()

# Initialize Ollama client with the specified host
# We use 127.0.0.1:11434 as the default to ensure it hits the local Ollama instance
ollama_host = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')
print(f"Connecting to Ollama at: {ollama_host}")
client = Client(host=ollama_host)
# How long (seconds) to poll `ollama ps --json` waiting for a runner to appear
# Set via `OLLAMA_RUNNER_POLL_SECONDS`; default 30s which is safer for larger models.
try:
    OLLAMA_RUNNER_POLL_SECONDS = float(os.environ.get("OLLAMA_RUNNER_POLL_SECONDS", "30"))
except Exception:
    OLLAMA_RUNNER_POLL_SECONDS = 30.0
import datetime


def log_ollama_response_error(exc, model_name: str | None = None, request_info: dict | None = None) -> None:
    """Append structured information about an Ollama ResponseError to `ollama_errors.log`.

    This will attempt to extract `response.status_code` and `response.text` from
    the exception when available and write a JSON line for later inspection.
    """
    info = {
        "time": datetime.datetime.utcnow().isoformat() + "Z",
        "error_type": exc.__class__.__name__,
        "error_str": str(exc),
    }
    try:
        resp = getattr(exc, "response", None)
        if resp is not None:
            info["status_code"] = getattr(resp, "status_code", None)
            try:
                # `.text` may be large; include it for debugging
                info["response_text"] = getattr(resp, "text", None)
            except Exception:
                info["response_text"] = "<unreadable>"
    except Exception:
        pass

    if model_name:
        info["model"] = model_name
    if request_info:
        info["request"] = request_info

    try:
        with open("ollama_errors.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(info, ensure_ascii=False) + "\n")
    except Exception:
        logging.exception("Failed to write ollama error log")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize MCP client (loads home/local JSON configs then env) and autostart servers
    try:
        initialize_mcp_client(autostart=True)
        # After servers start, discover tools so prompt can list the real names
        get_mcp_client().refresh_tools()
    except Exception:
        pass
    yield


app = FastAPI(lifespan=lifespan)

# Resolve static directory relative to this file so packaged installs work
STATIC_DIR = Path(__file__).parent / "static"

# Memory file path
MEMORY_FILE = "bridge_memory.json"

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: str
    messages: List[Message]
    remember: bool = True
    stream_only: bool = False
    compress: Optional[bool] = None
    options: Optional[Dict[str, Any]] = None

    @field_validator('model')
    def validate_model(cls, v):
        if not v or not isinstance(v, str) or len(v.strip()) == 0:
            raise ValueError('Model name must be a non-empty string')
        if len(v) > 100:  # Reasonable limit
            raise ValueError('Model name too long')
        # Basic sanitization - no control characters
        if any(ord(c) < 32 for c in v):
            raise ValueError('Model name contains invalid characters')
        return v.strip()

    @field_validator('messages')
    def validate_messages(cls, v):
        if not v or len(v) == 0:
            raise ValueError('At least one message is required')
        if len(v) > 50:  # Reasonable conversation limit
            raise ValueError('Too many messages in conversation')
        total_content_length = sum(len(msg.content) for msg in v if msg.content)
        if total_content_length > 100000:  # ~100KB limit
            raise ValueError('Total message content too large')
        return v

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            logging.error(f"Memory file {MEMORY_FILE} is corrupted: {e}. Backing up and starting fresh.")
            # Backup the corrupted file
            import datetime
            backup_name = f"{MEMORY_FILE}.corrupted.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            try:
                os.rename(MEMORY_FILE, backup_name)
                logging.info(f"Corrupted memory file backed up as {backup_name}")
            except OSError as backup_e:
                logging.error(f"Failed to backup corrupted memory file: {backup_e}")
            return []
    return []


def load_heritage_context() -> str:
    """Best-effort load of heritage context; tolerate missing or empty/invalid JSON."""
    h_path = "heritage_context.json"
    if not os.path.exists(h_path):
        return ""

    import fcntl
    try:
        with open(h_path, "r") as f:
            # Acquire shared lock for reading
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            raw = f.read().strip()
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # Release lock
            if not raw:
                return ""
            h_data = json.loads(raw)
            return "\n".join([item.get("content", "") for item in h_data if isinstance(item, dict)])
    except (json.JSONDecodeError, ValueError, OSError):
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
    import tempfile
    try:
        # Write to temp file first
        with tempfile.NamedTemporaryFile(mode='w', dir=os.path.dirname(MEMORY_FILE) or '.', suffix='.tmp', delete=False) as f:
            json.dump(memory, f, indent=2)
            temp_path = f.name
        # Atomic move
        os.replace(temp_path, MEMORY_FILE)
        logging.info(f"Successfully saved memory with {len(memory)} messages")
    except Exception as e:
        logging.error(f"Failed to save memory: {e}")
        # Clean up temp file if it exists
        if 'temp_path' in locals():
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise  # Re-raise to let caller handle it

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

        # Heuristics and Settings
        suggested = suggest_settings(request.model)
        
        # Merge user options with suggested options (user overrides)
        final_options = {
            "num_ctx": suggested["num_ctx"],
            "num_predict": suggested["num_predict"],
            "temperature": suggested["temperature"]
        }
        if request.options:
            final_options.update(request.options)
            
        # Compression logic
        should_compress = request.compress
        if should_compress is None:
            # Auto-detect: compress if model is small (<= 3B)
            parsed = parse_model_name(request.model)
            if parsed["size"] != "unknown":
                try:
                    size_val = float(parsed["size"].rstrip('B'))
                    if size_val <= 3:
                        should_compress = True
                except ValueError:
                    pass

        # Prepare messages for Ollama
        system_prompt = (
            "You are the voice of The Bridge, a contemplative AI interface for the 'Dirt' project. "
            f"{heritage_context} "
            "Your goal is to help the user weave their thoughts and remember their insights. "
            "Tooling: If you are tool-capable, you can call MCP tools on the configured servers "
            f"({servers_text}). When the user says \"Use the Bridge\" or asks to log/remember sessions, "
            "first call tool mcp-bridge_bridge_start_session on server mcp-bridge, then use other mcp-bridge_* "
            "tools as needed. Only call mcp-bridge_bridge_log_meditation or mcp-bridge_bridge_log_consult when the user explicitly asked and you can supply the required fields (emergentSentence + contextWords, or meditationText/mcpResult). If you cannot satisfy the required fields, do not call those tools; instead, reply in text. If you cannot call tools, continue with a helpful text response. "
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
        # Apply compression to user messages if needed
        request_messages = []
        for m in request.messages:
            content = m.content
            if should_compress and m.role == "user":
                content = compress_prompt(content)
            request_messages.append({"role": m.role, "content": content})

        full_messages = context_messages + request_messages

        tools = mcp_tools_schema()
        tool_capable = not request.stream_only
        tool_warning = ""

        print(f"Sending request to Ollama model: {request.model} at {client._client.base_url} (streaming with tools={tool_capable})")

        def stream_with_tools():
            nonlocal tool_warning
            content_total = ""
            try:
                max_tool_hops = 4
                hop = 0
                while hop < max_tool_hops:
                    hop += 1
                    hop_content = ""
                    tool_calls = []

                    # Try client.chat; if it fails immediately, retry once without tools.
                    stream = None
                    for attempt in (0, 1):
                        try:
                            stream = client.chat(
                                model=request.model,
                                messages=full_messages,
                                tools=(tools if tool_capable and attempt == 0 else None),
                                stream=True,
                                options=final_options,
                            )
                            # success
                            if attempt == 1 and tool_capable:
                                # we retried without tools; warn the user
                                tool_warning = f"Warning: model {request.model} failed with tools; retrying without tools.\n"
                                yield tool_warning
                                content_total += tool_warning
                                hop_content += tool_warning
                            break
                        except Exception as e:
                            # Log the failure
                            try:
                                log_ollama_response_error(e, model_name=request.model, request_info={"phase": f"client.chat init attempt {attempt}"})
                            except Exception:
                                logging.exception("Failed to write structured ollama error log during retry")

                            # If this was the first attempt, poll for the runner to appear before retrying
                            if attempt == 0:
                                # Poll `ollama ps --json` for up to the configured timeout
                                poll_deadline = time.time() + float(OLLAMA_RUNNER_POLL_SECONDS)
                                runner_seen = False
                                while time.time() < poll_deadline:
                                    try:
                                        proc = subprocess.run(["ollama", "ps", "--json"], capture_output=True, text=True, check=False)
                                        out = (proc.stdout or "").strip()
                                        if out:
                                            try:
                                                data = json.loads(out)
                                                # data may be a dict with 'runners' or a list
                                                candidates = []
                                                if isinstance(data, dict):
                                                    # flatten possible fields
                                                    for k in ("runners", "models", "items"): 
                                                        if k in data and isinstance(data[k], list):
                                                            candidates = data[k]
                                                            break
                                                    if not candidates and isinstance(data.get("models"), list):
                                                        candidates = data.get("models", [])
                                                elif isinstance(data, list):
                                                    candidates = data

                                                for entry in candidates:
                                                    # entry may have fields like 'model', 'image', or 'name'
                                                    text_fields = []
                                                    if isinstance(entry, dict):
                                                        for fld in ("model", "image", "name"):
                                                            v = entry.get(fld)
                                                            if isinstance(v, str):
                                                                text_fields.append(v)
                                                    elif isinstance(entry, str):
                                                        text_fields.append(entry)

                                                    for t in text_fields:
                                                        if request.model in t:
                                                            runner_seen = True
                                                            break
                                                    if runner_seen:
                                                        break
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                    if runner_seen:
                                        break
                                    time.sleep(0.5)

                                if not runner_seen:
                                    logging.info(f"Runner for model {request.model} not detected after polling; will still retry once.")

                            # If this was the second attempt, surface a marker and abort
                            if attempt == 1:
                                marker = f"\n[model init error after retry: {str(e)}]\n"
                                yield marker
                                content_total += marker
                                return
                            # Otherwise, continue to retry without tools
                    

                    try:
                        for part in stream:
                            msg = part.get("message", {})
                            delta = msg.get("content", "") or part.get("response", "") or ""
                            if delta:
                                hop_content += delta
                                content_total += delta
                                yield delta

                            # `last_message` intentionally not used; skip storing it
                            tool_calls = msg.get("tool_calls", []) or []
                            if tool_calls:
                                break  # pause to execute tools

                    except Exception as e:
                        # If the ollama client signals the model runner crashed, emit a readable marker
                        is_response_error = ResponseError is not None and isinstance(e, ResponseError)
                        if not is_response_error and e.__class__.__name__ == 'ResponseError':
                            is_response_error = True

                        if is_response_error:
                            # Log structured details for later inspection
                            try:
                                log_ollama_response_error(e, model_name=request.model, request_info={"messages_count": len(full_messages)})
                            except Exception:
                                logging.exception("Failed to write structured ollama error log")

                            err_text = f"\n[model runner error: {str(e)}]\n"
                            yield err_text
                            content_total += err_text
                            break
                        raise

                    # Record assistant hop content before tool execution
                    full_messages.append({"role": "assistant", "content": hop_content})

                    if not tool_calls:
                        break

                    # Execute tool calls and append results
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
                            bridge_blocked = False
                            if server_name == "mcp-bridge" and tool_name in {"bridge_log_meditation", "bridge_log_consult"}:
                                if tool_name == "bridge_log_meditation":
                                    has_sentence = bool(tool_args.get("emergentSentence"))
                                    has_context = bool(tool_args.get("contextWords"))
                                    has_text = bool(tool_args.get("meditationText"))
                                    has_mcp = bool(tool_args.get("mcpResult"))
                                    if not ((has_sentence and has_context) or has_text or has_mcp):
                                        bridge_blocked = True
                                        tool_result = {
                                            "error": "bridge_log_meditation_missing_fields",
                                            "detail": "Provide emergentSentence+contextWords, or meditationText/mcpResult."
                                        }
                                elif tool_name == "bridge_log_consult":
                                    has_model = bool(tool_args.get("model"))
                                    has_prompt = bool(tool_args.get("prompt"))
                                    has_resp = bool(tool_args.get("response") or tool_args.get("consultText") or tool_args.get("mcpResult"))
                                    if not (has_model and has_prompt and has_resp):
                                        bridge_blocked = True
                                        tool_result = {
                                            "error": "bridge_log_consult_missing_fields",
                                            "detail": "Provide model + prompt + response/consultText/mcpResult."
                                        }

                            if not bridge_blocked:
                                tool_result = mcp_client.call_tool(server_name, tool_name, tool_args)
                                if tool_result is None:
                                    tool_result = {"error": f"Tool call failed for {server_name}:{tool_name}"}

                        tool_message = {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id"),
                            "content": json.dumps(tool_result)
                        }
                        full_messages.append(tool_message)
                        # Stream a brief marker for the user
                        marker = f"\n[tool {name} -> {tool_name if name=='call_mcp_tool' else ''}]\n"
                        yield marker
                        content_total += marker

                # end while
            finally:
                if request.remember and content_total:
                    assistant_message = {"role": "assistant", "content": content_total}
                    memory.append(request.messages[-1].model_dump())
                    memory.append(assistant_message)
                    save_memory(memory)

        return StreamingResponse(stream_with_tools(), media_type="text/plain")
    except Exception as e:
        # Log structured details for unexpected failures in the chat endpoint
        try:
            log_ollama_response_error(e, model_name=(request.model if 'request' in locals() else None), request_info={"phase": "chat_endpoint"})
        except Exception:
            logging.exception("Failed to write structured ollama error log in chat endpoint")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

class SeedRequest(BaseModel):
    content: str
    tags: List[str] = []

    @field_validator('content')
    def validate_content(cls, v):
        if not v or not isinstance(v, str) or len(v.strip()) == 0:
            raise ValueError('Content must be a non-empty string')
        if len(v) > 10000:  # Reasonable content limit
            raise ValueError('Content too long')
        return v.strip()

    @field_validator('tags')
    def validate_tags(cls, v):
        if len(v) > 10:  # Reasonable tag limit
            raise ValueError('Too many tags')
        for tag in v:
            if not isinstance(tag, str) or len(tag.strip()) == 0:
                raise ValueError('Tags must be non-empty strings')
            if len(tag) > 50:
                raise ValueError('Tag name too long')
        return [tag.strip() for tag in v]

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


@app.get("/debug/ollama-errors")
async def get_ollama_errors(lines: int = 20):
    """Return the last N structured Ollama error log entries (JSON lines).

    `lines` controls how many most-recent entries to return (default 20).
    If the log contains non-JSON lines they are returned as `{"raw": "..."}` entries.
    """
    log_path = "ollama_errors.log"
    if not os.path.exists(log_path):
        return []

    entries = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    entries.append(json.loads(ln))
                except Exception:
                    entries.append({"raw": ln})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read ollama error log: {e}")

    if lines <= 0:
        return entries
    return entries[-lines:]

# MCP Server Management Endpoints

class MCPServerAction(BaseModel):
    server_name: str

class MCPToolCall(BaseModel):
    server_name: str
    tool_name: str
    arguments: Optional[Dict[str, Any]] = None

    @field_validator('server_name')
    def validate_server_name(cls, v):
        if not v or not isinstance(v, str) or len(v.strip()) == 0:
            raise ValueError('Server name must be a non-empty string')
        if len(v) > 50:  # Reasonable limit
            raise ValueError('Server name too long')
        # Basic sanitization - alphanumeric, hyphens, underscores only
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError('Server name contains invalid characters')
        return v.strip()

    @field_validator('tool_name')
    def validate_tool_name(cls, v):
        if not v or not isinstance(v, str) or len(v.strip()) == 0:
            raise ValueError('Tool name must be a non-empty string')
        if len(v) > 100:  # Reasonable limit
            raise ValueError('Tool name too long')
        # Basic sanitization - alphanumeric, underscores only
        import re
        if not re.match(r'^[a-zA-Z0-9_]+$', v):
            raise ValueError('Tool name contains invalid characters')
        return v.strip()


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


class DreamRequest(BaseModel):
    path: str
    pattern: Optional[str] = "**/*.md"
    length: int = 10
    seed: Optional[str] = None

    @field_validator('path')
    def validate_path(cls, v):
        if not v or not isinstance(v, str) or len(v.strip()) == 0:
            raise ValueError('Path must be a non-empty string')
        return v.strip()

    @field_validator('length')
    def validate_length(cls, v):
        if not isinstance(v, int) or v <= 0 or v > 1000:
            raise ValueError('Length must be a positive integer <= 1000')
        return v


@app.post("/dream")
async def weave_dream(request: DreamRequest):
    """Call the dream-weaver MCP tool to weave a short dream from local files."""
    mcp_client = get_mcp_client()
    args = {"path": request.path, "pattern": request.pattern, "length": request.length}
    if request.seed:
        args["seed"] = request.seed

    result = mcp_client.call_tool("dream-weaver", "weave_dream", args)
    if result is None:
        raise HTTPException(status_code=500, detail="Dream weaving failed")
    return result


@app.post("/seed")
async def seed_content(request: SeedRequest):
    """Save a piece of content into the Bridge memory (seed/heritage).

    The frontend uses this to store tool outputs or user-provided snippets.
    """
    try:
        memory = load_memory()
        # Use a simple schema: store as user-provided content with optional tags
        entry = {"role": "assistant", "content": request.content, "tags": request.tags}
        memory.append(entry)
        save_memory(memory)
        return {"status": "seeded", "count": len(memory)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to seed content: {e}")

# Serve static files
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def run():
    """Run the Dirt FastAPI app with uvicorn."""
    import uvicorn

    host = os.environ.get("DIRT_HOST", "0.0.0.0")
    port = int(os.environ.get("DIRT_PORT", "8000"))
    # When this file is executed as a script, the package import path
    # may not contain the project root which causes uvicorn to fail
    # importing "app.main:app". Pass the `app` object directly so
    # uvicorn uses the already-imported ASGI app instance.
    uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
