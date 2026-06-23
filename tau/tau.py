#!/usr/bin/env python3
"""
tau bot - Main Entry Point for the Tau AI Agent System.

This module provides the primary command-line interface for the tau bot,
a standalone AI agent with tool calling capabilities designed for integration
with llama.cpp and vLLM backends.

Overview
--------
tau bot is a conversational AI agent that can interact with users through
a command-line interface, execute tools, manage context, and spawn subagents
for complex tasks. It features:

- **Tool Calling**: Dynamically loads tools from the tools/ directory and
  skill tools from the skills/ directory
- **Context Management**: Maintains conversation history with configurable
  context limits and persistence
- **A2A (Agent-to-Agent) Communication**: Built-in server for agent discovery
  and inter-agent communication via Unix sockets
- **Subagent Support**: Can spawn child agents (forks/subagents) for parallel
  task execution
- **Loop Detection**: Automatic detection and handling of conversation loops
- **Flexible LLM Configuration**: Support for multiple LLM backends with
  runtime switching via command-line arguments

Key Components
--------------
TauBot (agent_core):
    Core agent class handling tool calling, context management, and the main
    execution loop. Manages LLM interactions, tool execution, and subagent
    spawning.

A2AServer (agent_a2a):
    Agent-to-Agent communication server that enables agent discovery and
    inter-agent communication via Unix domain sockets.

Command-Line Interface
----------------------
The module provides a rich CLI with the following options:

    Usage:
        python tau.py [OPTIONS] [inputs ...]

    Core Options:
        --llm GROUP        LLM group name (default: first available group)
        --base-url URL     API base URL (overrides config)
        --model MODEL      Model name (overrides config)
        --ctx TOKENS       Maximum context size in tokens
        --heartbeat SECS   Heartbeat interval for idle detection
        --debug            Enable debug output

    Context Options:
        -c, --continue     Continue from saved context file
        inputs ...         Input messages (non-interactive mode)

    A2A Query Options:
        --list             List active agents
        --list-all         List all agents including stale
        --listjson         List active agents (JSON format)
        --listjson-all     List all agents (JSON format)
        --pid PID          Query agent by PID
        --name NAME        Query agent by name
        --card             Get agent card (JSON format)
        --timeout SECS     Query timeout

    Server Options:
        --keep-alive       Keep running when stdin is closed (A2A mode)
        --agent-name NAME  Agent instance name

Configuration
-------------
Configuration is loaded from tau.json in the project root, with support for:
- LLM groups with different backends (base_url, model, context limits)
- Agent naming and logging configuration
- Malformed response handling thresholds
- Inference parameters

Example Configuration (tau.json):
    {
        "llm_groups": {
            "llama": {
                "api_base": "http://localhost:8080/v1",
                "model": "llama-3.1-70b",
                "max_context_tokens": 8192
            }
        },
        "agent_name": "tau-bot"
    }

Usage Examples
--------------
1. Interactive mode with default configuration:
    $ python tau.py
    # Agent starts and waits for user input

2. Non-interactive mode with input:
    $ python tau.py "What is quantum computing?"
    # Processes input and exits

3. Using a specific LLM group:
    $ python tau.py --llm llama "Analyze this code"
    # Uses the 'llama' group from configuration

4. Continuing from saved context:
    $ python tau.py -c
    # Restores previous conversation from context file

5. Querying agent status:
    $ python tau.py --list
    # Lists all active tau bots

6. A2A mode with keep-alive:
    $ python tau.py --keep-alive
    # Runs as a persistent A2A server

Architecture
------------
The agent follows a layered architecture:

    ┌─────────────────────────────────────────────────────────┐
    │                    Command Line Interface               │
    ├─────────────────────────────────────────────────────────┤
    │                    TauBot Core                        │
    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
    │  │  Tool       │  │  Context    │  │  LLM Client     │  │
    │  │  Manager    │  │  Manager    │  │                 │  │
    │  └─────────────┘  └─────────────┘  └─────────────────┘  │
    ├─────────────────────────────────────────────────────────┤
    │              A2A Server (Unix Socket)                   │
    └─────────────────────────────────────────────────────────┘

Execution Flow
--------------
1. Parse command-line arguments and resolve LLM configuration
2. Initialize TauBot with selected LLM group
3. Start A2A server for agent discovery
4. Restore context if requested (--continue flag)
5. Process input messages or enter interactive mode
6. Run main loop: receive input → generate response → execute tools
7. Handle graceful shutdown on KeyboardInterrupt
8. Cleanup A2A server and exit

Error Handling
--------------
- Malformed LLM responses trigger retry logic with increasing verbosity
- Tool execution errors are caught and reported to the agent
- Network errors trigger automatic retry with backoff
- Context overflow is handled by automatic LLM-based compression

See Also
--------
- agent_core.py: TauBot class implementation
- agent_a2a.py: A2A server implementation
- agent_config.py: Configuration management
- tools/: Available tool implementations
- skills/: Skill-based capabilities

Author
------
tau bot Development Team

Version
-------
1.0.0
"""

import argparse
import os
import sys
import traceback

from agent_a2a import A2AServer, a2a_cli_mode
from agent_config import get_config
from agent_core import TauBot
from agent_input import get_context_file_by_parent_ppid
from agent_models import InputMessage
from agent_console import (
    a2a_started_message,
    assistant_message_display,
    context_restore_failure,
    context_restored,
    error,
    interrupted_message,
    no_context_file_found,
    print_agent_exit_summary,
    tools_loaded_message,
    user_message_display,
)
from agent_console_primitives import status
from agent_version import get_version_info

# Force line-buffered stdout so output appears immediately (not blocked until 8KB fills)
sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]


def _resolve_llm_group(config) -> str | None:
    """Resolve --llm from CLI args, falling back to config default.

    Uses a pre-parser with add_help=False so --help is not consumed.
    """
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--llm")
    known, _ = pre_parser.parse_known_args()
    return known.llm or config.llm_group_name
def _validate_llm_group(config, llm_group_name: str | None) -> None:
    """Validate that the resolved LLM group exists. Exit if not."""
    if config.llm_groups and llm_group_name:
        active_group = config.llm_groups.get(llm_group_name)
        if active_group is not None:
            return  # Valid group
    error(
        f"No active LLM group '{llm_group_name}'. "
        f"Available: {', '.join(sorted(config.llm_groups.keys())) if config.llm_groups else '(none)'}. "
        f"Define groups in tau.json under 'llm_groups'."
    )
    sys.exit(1)

def main():
    """Main entry point."""
    # Load configuration from file + env vars (layer 1-2)
    config = get_config()

    # Resolve --llm from known args BEFORE building the full parser
    llm_group_name = _resolve_llm_group(config)
    _validate_llm_group(config, llm_group_name)
    active_group = config.llm_groups[llm_group_name]

    parser = argparse.ArgumentParser(
        description="Standalone CLI agent with tool calling."
    )

    # Use None defaults so we can distinguish "user explicitly set" vs "group default"
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"Base URL of the API (default: {active_group.api_base})",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"Model name (default: {active_group.model})",
    )
    parser.add_argument(
        "--ctx",
        type=int,
        default=None,
        help=f"Maximum context size in tokens (default: {active_group.max_context_tokens:,})",
    )
    parser.add_argument(
        "--heartbeat",
        type=int,
        default=None,
        help="Heartbeat interval in seconds (idle before fork). 0 or omit to disable.",
    )
    parser.add_argument(
        "--llm",
        default=llm_group_name,
        help="LLM group name to use (default: from config or first group)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument(
        "-c",
        "--continue",
        dest="continue_from_file",
        action="store_true",
        help="Continue from saved context file (LOG_DIR/{prefix}.context)",
    )
    parser.add_argument(
        "inputs", nargs="*", help="Input messages to process (non-interactive mode)"
    )
    parser.add_argument(
        "--keep-alive",
        action="store_true",
        help="Keep agent running even when stdin is closed (for A2A mode)",
    )
    # A2A client flags
    parser.add_argument(
        "--list", action="store_true", help="List only active agents (human-readable)"
    )
    parser.add_argument(
        "--list-all",
        action="store_true",
        help="List all agents including unreachable/stale (human-readable)",
    )
    parser.add_argument(
        "--listjson", action="store_true", help="List only active agents (JSON format)"
    )
    parser.add_argument(
        "--listjson-all",
        action="store_true",
        help="List all agents including unreachable/stale (JSON format)",
    )
    parser.add_argument("--name", help="Agent name to query (instead of --pid)")
    parser.add_argument(
        "--agent-name",
        default=config.agent_name,
        help="Agent name for this instance (default: from config or 'default')",
    )
    parser.add_argument(
        "--pid", type=int, help="Agent PID to query (required for --card)"
    )
    parser.add_argument(
        "--card", action="store_true", help="Get agent card (JSON format)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=config.timeout,
        help="Query timeout in seconds (default: from config)",
    )

    args = parser.parse_args()

    # Validate --llm group name against available groups
    if args.llm and config.llm_groups and args.llm not in config.llm_groups:
        available = ", ".join(sorted(config.llm_groups.keys()))
        error(f"Unknown LLM group '{args.llm}'. Available: {available}")
        sys.exit(1)

    # Handle A2A client mode
    if any(
        (
            args.list,
            args.list_all,
            args.listjson,
            args.listjson_all,
            args.pid,
            args.name,
        )
    ):
        a2a_cli_mode(args)

    # Load tools

    # Create agent with config (pass None for group-defaulted values so /llm can switch)
    agent = TauBot(
        config=config,
        base_url=args.base_url,  # None = use group value, no sticky override
        model=args.model,  # None = use group value, no sticky override
        max_context_tokens=args.ctx,  # None = use group value, no sticky override
        agent_name=args.agent_name,
        llm_group_name=args.llm,
        heartbeat_seconds=args.heartbeat,
    )

    base_url = args.base_url if args.base_url is not None else active_group.api_base
    model = args.model if args.model is not None else active_group.model
    ctx = args.ctx if args.ctx is not None else active_group.max_context_tokens
    version_info = get_version_info()
    status(version_info["version_str"])
    status(
        f"[Tool Agent Starting] Model: {model} | API: {base_url} | Context: {ctx:,} tokens | Agent PID: {os.getpid()}"
    )
    tools_loaded_message(len(agent.available_tool_names))

    # Start A2A server BEFORE context restore
    a2a_server = A2AServer(agent)
    a2a_server.start()
    a2a_started_message(a2a_server.sock_path)

    if args.continue_from_file:
        target_ctx = get_context_file_by_parent_ppid()
        if target_ctx:
            agent._session.context_file = target_ctx
            if agent.context.load_from_file(agent._session.context_file):
                context_restored(len(agent.context), target_ctx)
                for role, display_fn in [
                    ("user", user_message_display),
                    ("assistant", assistant_message_display),
                ]:
                    msgs = [m for m in agent.context if m.get("role") == role]
                    if msgs:
                        display_fn(msgs[-1].get("content", ""))
            else:
                context_restore_failure(target_ctx)
        else:
            no_context_file_found()

    # Put command-line inputs in queue
    if args.inputs:
        for inp in args.inputs:
            message = InputMessage.from_command_line(inp)
            agent.input_queue.put(message)

    # Handle Ctrl-C
    try:
        agent.run(
            inputs=args.inputs,
            a2a_server=a2a_server,
            keep_alive=args.keep_alive,
            interactive=not bool(args.inputs),
        )
    except KeyboardInterrupt:
        print_agent_exit_summary(agent)
        interrupted_message()
    except (RuntimeError, OSError, ValueError, TypeError) as e:
        error(str(e))
        traceback.print_exc()
    except SystemExit as e:
        # Re-raise SystemExit(0) normally (clean exit). Log non-zero exits.
        if e.code and e.code != 0:
            error(f"SystemExit({e.code})")
            traceback.print_exc()
        raise
    except BaseException as e:
        # Catch-all for ANY exception that slipped through (e.g., AttributeError, KeyError, etc.)
        # This ensures we ALWAYS log the error before dying.
        error(f"FATAL: {type(e).__name__}: {e}")
        traceback.print_exc()
        raise
    finally:
        a2a_server.stop()

    sys.exit(0)

if __name__ == "__main__":
    main()