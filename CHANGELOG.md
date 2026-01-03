# Changelog

## [0.2.0] - 2026-01-02
### Added
- Chat endpoint now supports MCP tool-calls via the `call_mcp_tool` schema with argument handling for dict or string payloads.
- MCP servers auto-start at app startup and run a `tools/list` discovery pass to surface exact tool names.
- System prompt now enumerates discovered servers and tools so models avoid "unknown tool" errors.
- JSON-RPC tool calling and response handling for stdio MCP servers.

### Changed
- README refreshed to describe the current FastAPI chat flow, CLI/API paths, and stdio server usage.
- Default install uses `pip install -e .` and references the repo’s sample `mcp_config.json`.

### Fixed
- Tool calling no longer 500s when models send dict-style arguments.
- Tool discovery tests start real servers (dream-weaver, creative-meditate, resonance-engine, mcp-bridge, ollama-consult) and validate `tools/list`.

## [0.1.0] - 2025-xx-xx
- Initial release.
