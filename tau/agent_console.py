"""Consolidated console display module for TauBot.

Re-exports everything from agent_console_messages.py (declarative message
templates) and agent_console_display.py (complex display functions).

This facade preserves backward compatibility: all existing imports from
agent_console continue to work without modification.

agent_console_primitives.py remains as the foundation layer.
agent_console_audit.py remains separate (complex audit parsing logic).
"""
from __future__ import annotations

# ── Re-export all declarative message templates ──────────────────────────────
import agent_console_messages  # noqa: F401 (needed for __all__ composition)
from agent_console_messages import (
    # Registry
    _ConsoleMessage,
    _msg,
    # Error display
    error,
    warning,
    invalid_mode_error,
    unknown_command_error,
    unknown_tool_error,
    no_run_function_error,
    command_file_not_found,
    log_dir_error,
    exec_tool_fail,
    dynamic_command_result,
    # Message display
    user_echo,
    assistant_message_display,
    user_message_display,
    # Flow control
    restart_flow_info,
    restart_flow_success,
    restart_failure,
    restart_fallback_failure,
    force_exit_message,
    interrupted_message,
    # Loop warnings
    loop_warning_display,
    # Subagent/fork
    subagent_start_display,
    subagent_usage,
    fork_usage,
    fork_display,
    subagent_output_header,
    subagent_output_footer,
    subagent_error,
    fork_error,
    # A2A/agent
    agents_json,
    agent_card_json,
    agent_status_message,
    a2a_cli_error,
    a2a_started_message,
    agent_a2a_response,
    # Compression
    compress_success,
    compress_fail,
    # Context display
    context_cleared_success,
    context_restored,
    # Tool display
    tool_result,
    no_tools_message,
    shell_tool_not_available,
    shell_command_usage,
    tools_loaded_message,
    tool_blocked,
    exec_usage,
)

# ── Re-export all complex display functions ──────────────────────────────────
import agent_console_display  # noqa: F401 (needed for __all__ composition)
from agent_console_display import (
    # Simple functions
    error_display,
    undo_message,
    restart_flow,
    # Loop warnings
    loop_warning,
    # LLM status
    llm_timeout_message,
    llm_validation_retry,
    # Compression display
    compression_step_summary,
    # Context display
    context_dump,
    context_summary_stats,
    context_status_bar,
    context_validation_warning,
    context_append_warning,
    context_restore_failure,
    no_context_file_found,
    context_list_display,
    context_preview_display,
    context_validation_display,
    context_recovery_display,
    context_dump_with_json,
    # Status display
    agent_status,
    exit_summary,
    print_agent_exit_summary,
    print_context_status,
    build_cache_hit_rates_str,
    # Help display
    help_display,
    show_help,
    show_commands,
    show_tools,
    show_tools_json,
    show_agent_card,
    show_command_help,
    # Tool display
    tool_start,
    tool_output,
    tool_error_detail,
    tools_listing,
    tools_json_schema,
    # Table/A2A display
    agents_table_header,
    agents_table_row,
)

# ── __all__ exports (dynamic composition — single source of truth) ───────────
# Composed from sub-module __all__ lists. Adding a function requires editing
# only the originating module's __all__, not this facade.

__all__ = [
    # From agent_console_messages (declarative templates)
    *agent_console_messages.__all__,
    # From agent_console_display (complex display functions)
    *agent_console_display.__all__,
]
