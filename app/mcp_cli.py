#!/usr/bin/env python3
"""
Command Line Interface for the Pluggable MCP Client System

Usage:
    python mcp_cli.py list                    # List configured servers
    python mcp_cli.py start <server_name>     # Start a server
    python mcp_cli.py stop <server_name>      # Stop a server
    python mcp_cli.py call <server> <tool>    # Call a tool on a server
    python mcp_cli.py config <server_name>    # Show server configuration

Environment Variables:
    MCP_SERVERS=server1,server2,server3
    MCP_SERVER_{NAME}_COMMAND=command
    MCP_SERVER_{NAME}_ARGS=["arg1", "arg2"]
    MCP_SERVER_{NAME}_ENV={"VAR": "value"}
    MCP_SERVER_{NAME}_NODE_MODULES=/path/to/node_modules

Or use JSON config file:
    python mcp_cli.py --config mcp_config.json <command>
"""

import sys
import json
import argparse
from typing import Optional

try:
    # Package import
    from app.mcp_client import initialize_mcp_client, get_mcp_client
except ImportError:  # pragma: no cover - fallback for direct script execution
    from mcp_client import initialize_mcp_client, get_mcp_client

def list_servers():
    """List all configured and active MCP servers."""
    client = get_mcp_client()
    configured = client.list_servers()
    active = client.list_active_servers()

    print("Configured MCP Servers:")
    for server in configured:
        status = "ACTIVE" if server in active else "INACTIVE"
        print(f"  - {server} [{status}]")

    print(f"\nTotal configured: {len(configured)}, Active: {len(active)}")

def start_server(server_name: str):
    """Start an MCP server."""
    client = get_mcp_client()
    print(f"Starting MCP server: {server_name}...")
    success = client.start_server(server_name)
    if success:
        print(f"✓ Successfully started {server_name}")
    else:
        print(f"✗ Failed to start {server_name}")
        sys.exit(1)

def stop_server(server_name: str):
    """Stop an MCP server."""
    client = get_mcp_client()
    print(f"Stopping MCP server: {server_name}...")
    success = client.stop_server(server_name)
    if success:
        print(f"✓ Successfully stopped {server_name}")
    else:
        print(f"✗ Failed to stop {server_name}")
        sys.exit(1)

def call_tool(server_name: str, tool_name: str, args_json: Optional[str] = None):
    """Call a tool on an MCP server."""
    client = get_mcp_client()

    # Parse arguments if provided
    arguments = {}
    if args_json:
        try:
            arguments = json.loads(args_json)
        except json.JSONDecodeError as e:
            print(f"Error parsing arguments JSON: {e}")
            sys.exit(1)

    print(f"Calling tool '{tool_name}' on server '{server_name}' with args: {arguments}")

    result = client.call_tool(server_name, tool_name, arguments)
    if result:
        print("Tool call result:")
        print(json.dumps(result, indent=2))
    else:
        print("✗ Failed to call tool")
        sys.exit(1)

def show_config(server_name: str):
    """Show configuration for a specific server."""
    client = get_mcp_client()
    config = client.get_server_config(server_name)

    if config is None:
        print(f"Server '{server_name}' not found")
        sys.exit(1)

    print(f"Configuration for MCP Server: {server_name}")
    print(f"  Command: {config.command}")
    print(f"  Args: {config.args}")
    print(f"  Environment: {json.dumps(config.env, indent=2)}")
    print(f"  Node Modules Path: {config.node_modules_path}")

def main():
    parser = argparse.ArgumentParser(
        description="Pluggable MCP Client System CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        '--config',
        type=str,
        help='Path to JSON configuration file (alternative to environment variables)'
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # list command
    subparsers.add_parser('list', help='List configured MCP servers')

    # start command
    start_parser = subparsers.add_parser('start', help='Start an MCP server')
    start_parser.add_argument('server_name', help='Name of the server to start')

    # stop command
    stop_parser = subparsers.add_parser('stop', help='Stop an MCP server')
    stop_parser.add_argument('server_name', help='Name of the server to stop')

    # call command
    call_parser = subparsers.add_parser('call', help='Call a tool on an MCP server')
    call_parser.add_argument('server_name', help='Name of the server')
    call_parser.add_argument('tool_name', help='Name of the tool to call')
    call_parser.add_argument('--args', help='JSON string of arguments for the tool')

    # config command
    config_parser = subparsers.add_parser('config', help='Show server configuration')
    config_parser.add_argument('server_name', help='Name of the server')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Initialize MCP client
    client = initialize_mcp_client(args.config)

    # Execute command
    try:
        if args.command == 'list':
            list_servers()
        elif args.command == 'start':
            start_server(args.server_name)
        elif args.command == 'stop':
            stop_server(args.server_name)
        elif args.command == 'call':
            call_tool(args.server_name, args.tool_name, args.args)
        elif args.command == 'config':
            show_config(args.server_name)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()