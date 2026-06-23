"""TauBot Core Module

Core functionality for TauBot system: tool calling, context management, hierarchical agent orchestration.

Key Components
- TauBot: Main agent class, orchestrates agent lifecycle
- ToolFilter: Filters tools via allowlist/blocklist
- invoke_with_tools(): Entry point for user interactions
- invoke_with_tools_loop(): Core tool calling loop
- run(): Starts agent

Architecture
Message-driven:
1. User Input: Messages appended via invoke_with_tools(), validated for OpenAI compliance
2. LLM Interaction: invoke_with_tools_loop() manages request/response cycle, tool calling, retry logic
3. Tool Execution: execute_tool_batch() parses tool calls, appends results to context
4. Context Management: TauContext maintains history, auto-compresses near token limits
5. Hierarchical Delegation: Subagents (isolated), Fork (inherits parent), Delegate mode (orchestrator)
6. Safety: Loop detection, end-of-turn validation, interrupt/exit flags

Entry Points
- invoke_with_tools(user_input): Send message, execute tools, return response
- invoke_with_tools_loop(): Core loop (context must end with user message)
- run(inputs, interactive): Start agent with input handling

Commands
Slash commands (/help, /exit, /subagent) dispatched via _handle_command(). Custom commands loadable from commands/ directory.

Tools
Registered in TOOLS registry, filtered via ToolFilter. execute_tool_batch() handles parallel execution with error handling.

Thread Safety
System-wide flags (_interrupted, _exit_requested) for cooperative shutdown. Heartbeat runs in separate threads.

Example
    from agent_core import TauBot
    from agent_audit_bridge import log_console_warning
from agent_config import Config
    config = Config.load()
    agent = TauBot(config=config, agent_name="my-agent")
    response = agent.invoke_with_tools("What can you help me with?")
    print(response)

See Also
- agent_context: Context management
- agent_tool_executor: Tool execution
- agent_subagent: Subagent and fork invocation
- agent_commands: Command handling
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from agent_console import (
    assistant_message_display,
    context_list_display,
    context_preview_display,
    context_restored,
    context_restore_failure,
    context_validation_display,
    context_recovery_display,
    error,
    exec_tool_fail,
    exec_usage,
    no_context_file_found,
    no_run_function_error,
    print_agent_exit_summary,
    print_context_status,
    restart_fallback_failure,
    restart_failure,
    restart_flow,
    show_help,
    undo_message,
    unknown_command_error,
    unknown_tool_error,
    warning,
)
from agent_console_primitives import (
    blank_line,
    echo,
    reasoning,
    verbose,
)
from agent_command_handlers import CommandHandlersMixin, _command
from agent_command_registry import discover_commands, find_command_conflicts
from agent_commands import CommandManager
from agent_config import Config
from agent_context import TauContext, get_last_real_user_prompt
from agent_endofturn_validate import ValidationErrorType, is_valid_end_of_turn
from agent_heartbeat import HeartbeatManager
from agent_init import resolve_agent_init
from agent_input import InputHandler
from agent_lifecycle import AgentLifecycle
from agent_llm import LLMCallConfig, SimpleOpenAIClient, _invoke_llm_with_retry
from agent_loop_detect import LoopDetector
from agent_loop_escalation import LoopEscalationManager
from agent_models import AgentStatus, InputMessage
from agent_reflection import ReflectionScheduler
from agent_session import AgentSessionManager, LOG_DIR, SESSION_PREFIX
from agent_tool_executor import execute_tool_batch
from agent_tool_filter import ToolFilter
from tools import TOOLS

if TYPE_CHECKING:
    from agent_session import AuditWriter

__all__ = [
    "ToolFilter",
    "TauBot",
]


class TauBot(CommandHandlersMixin):
    """Chat agent with tool calling, context management, loop detection, and subagent support.

    The TauBot is the main orchestrator for AI agent interactions, providing:
    - Tool calling with OpenAI function-calling format
    - Context management with automatic compression
    - Loop detection to prevent infinite cycles
    - Subagent and fork support for hierarchical task delegation
    - Command handling for interactive control
    - Heartbeat mechanism for idle detection

    Attributes:
        config: Configuration object for the agent.
        agent_name: Name identifier for this agent instance.
        tool_filter: ToolFilter instance for filtering available tools.
        context: TauContext instance holding conversation history.
        client: SimpleOpenAIClient for LLM communication.
        loop_detector: LoopDetector for detecting conversation loops.
        available_tool_names: List of currently available tool names.
    """

    def __init__(
        self,
        config: Config | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_context_tokens: int | None = None,
        agent_name: str | None = None,
        tool_filter: "ToolFilter | None" = None,
        llm_group_name: str | None = None,
        heartbeat_seconds: int | None = None,
    ):
        """Initialize the TauBot with configuration and resources.

        Sets up the agent with the provided configuration, initializing the LLM
        client, context, tool filtering, and various subsystems. Configuration
        priority: explicit argument > config object > defaults/errors.

        Args:
            config: Configuration object containing LLM settings, loop detection,
                heartbeat, and other agent parameters.
            base_url: Optional override for LLM API base URL.
            model: Optional override for LLM model name.
            max_context_tokens: Optional override for maximum context size.
            agent_name: Optional name identifier for this agent instance.
            tool_filter: Optional ToolFilter instance for filtering tools.
                Defaults to ToolFilter() if not provided.
            llm_group_name: Optional LLM group name to use. Defaults to the
                first available group or config.llm_group_name.
            heartbeat_seconds: Optional heartbeat interval in seconds. If set,
                enables heartbeat checking for idle detection.

        Raises:
            ValueError: If no LLM group is found in the configuration.
        """
        # Resolve all config + overrides into a single, fully-resolved config.
        init = resolve_agent_init(
            config=config,
            base_url=base_url,
            model=model,
            max_context_tokens=max_context_tokens,
            agent_name=agent_name,
            llm_group_name=llm_group_name,
            heartbeat_seconds=heartbeat_seconds,
        )

        self.config = config
        self.agent_name = init.agent_name
        self.tool_filter = tool_filter or ToolFilter()
        self.llm_groups = init.llm_groups

        self.current_group_name = init.current_group_name
        self._llm_model_override = init.model_override
        self._llm_base_url_override = init.base_url_override
        self._llm_context_override = init.max_context_tokens_override

        self.model_name = init.model_name
        self.base_url = init.base_url
        self._current_api_key = init.api_key
        self.max_context_tokens = init.max_context_tokens

        self.max_silent_retries = init.max_silent_retries
        self.max_enhanced_retries = init.max_enhanced_retries
        self.max_explicit_retries = init.max_explicit_retries

        from agent_llm import PrefixCacheTracker

        self._cache_tracker = PrefixCacheTracker()
        self.client = SimpleOpenAIClient(
            base_url=self.base_url,
            api_key="",
            timeout=init.timeout,
            cache_tracker=self._cache_tracker,
        )
        self.context: TauContext = TauContext()

        self._sandbox_last_call: str | None = None  # sandbox double-call confirmation (see _run_sandbox_command)
        self.inference_params = init.inference_params

        self.max_tokens = init.max_tokens

        self._init_subsystems(init)

    # ── Subsystem initialization ──────────────────────────────────────────

    def _init_subsystems(self, init: "AgentInitConfig") -> None:
        """Initialize all agent subsystems.

        Creates and wires up: session manager, loop detector, reflection
        scheduler, loop escalation manager, heartbeat manager, and all
        supporting state (audit writer, system prompt, tool registry, etc.).

        Called once from ``__init__`` after config resolution.
        """
        # Session management (token tracking, context/audit file paths)
        self._session = AgentSessionManager()

        # Loop detection (sliding-window pattern matching)
        self.loop_detector = LoopDetector(
            window_size=init.loop_detection_window_size,
            repeat_threshold=init.loop_detection_repeat_threshold,
            replace_unknown_tools=init.loop_detection_replace_unknown_tools,
        )

        # Reflection scheduler (periodic self-reflection triggers)
        self.reflection_scheduler = ReflectionScheduler(
            init.reflection_config,
        )

        # Loop escalation (reactive recovery from detected loops)
        self._loop_escalation = LoopEscalationManager(
            loop_detector=self.loop_detector,
            reflection_scheduler=self.reflection_scheduler,
            context=self.context,
            agent=self,
        )

        # Nesting / CWD tracking
        self.nesting_count = 0
        self.original_cwd = Path.cwd()

        # Original task for subagent/fork tracking.
        # When non-None, this agent is a subagent or fork that was spawned to
        # execute a specific task. The original_task stores the prompt that was
        # given to the subagent/fork.
        self.original_task: str | None = None

        # Command-dispatch recursion depth (used by _dispatch_md guard).
        self._cmd_dispatch_depth = 0

        # Generic forced end-of-turn mechanism.
        # When set to a non-None string, invoke_with_tools_loop appends it as
        # the final assistant message and returns immediately.
        # Any tool can set this to force-exit the current turn.
        # Used by delegate mode (end_turn) to break the delegate loop.
        self.force_end_turn: str | None = None

        # ENDTURN sentinel resolution: tracks the last substantive assistant
        # message produced during the current turn. When end_turn(message="ENDTURN")
        # is called, this value becomes the final response so the model doesn't
        # have to repeat itself.
        # Set for ANY response with non-empty text (even responses that also have
        # tool calls). Never overwritten during recovery mode so that recovery-round
        # noise doesn't clobber the real answer the model already produced.
        self.last_substantive_response: str | None = None

        # Recovery mode flag: set when we inject an end_turn reminder, prevents
        # overwriting last_substantive_response during the recovery round.
        # Reset at the start of each invoke_with_tools_loop() call.
        self._recovery_active: bool = False

        # Audit writer initialization
        self._session.init_audit_writer()

        # A2A pending responses
        self._pending_a2a_responses: dict = {}

        # System prompt from AGENT.md
        agent_path = Path(__file__).resolve().parent / "AGENT.md"
        system_prompt = "You are helpful AI assistant. Do what User asks."
        if agent_path.exists():
            try:
                raw = agent_path.read_text().strip()
                system_prompt = raw.format(
                    log_file=self._session.audit_file,
                    audit_file=self._session.audit_file,
                    context_file=self._session.context_file,
                )
            except OSError as exc:
                print(f"WARNING: Could not read AGENT.md: {exc}", file=sys.stderr)
            except KeyError as exc:
                print(
                    f"WARNING: AGENT.md contains unescaped '{{' (missing placeholder: "
                    f"{exc}). Falling back to default system prompt.",
                    file=sys.stderr,
                )
        self.context.set_system(system_prompt)

        # Command / tool registration
        self.available_commands: dict[str, Any] = {}
        self._commands_directory = None
        self.available_tool_names = list(TOOLS.keys())
        self._register_skill_tools()

        # Log session start with full system prompt and tool schema
        self._session.audit_writer.session_start(
            model=self.model_name,
            tool_count=len(self.available_tool_names),
            cwd=os.getcwd(),
            system_prompt=self.context.get_system() or "",
            tool_schema=self.get_all_tools(),
        )

        # Input / threading state
        self.input_queue: queue.Queue = queue.Queue()
        self._a2a_listener_thread = None
        self._input_thread = None
        self._input_thread_stop = threading.Event()
        self._a2a_server = None
        self._keep_alive = False

        # Check for .py/.md command conflicts at startup
        from agent_command_registry import find_command_conflicts

        conflicts = find_command_conflicts()
        if conflicts:
            for name in conflicts:
                warning(
                    f"Command '{name}' exists as both .py and .md — .py takes precedence"
                )

        # Heartbeat (idle detection and auto-task execution)
        self._heartbeat = HeartbeatManager(
            enabled=init.heartbeat_enabled,
            interval_seconds=init.heartbeat_interval,
            agent=self,
        )

    def _register_skill_tools(self) -> None:
        """Register skill-related tools in the available tools list.

        Ensures that skill-related tools (currently only "skill") are included
        in the agent's available_tool_names list if they exist in the TOOLS registry.

        This method is called during initialization to guarantee skill tools
        are available for use.
        """
        for tool_name in ("skill",):
            if tool_name in TOOLS and tool_name not in self.available_tool_names:
                self.available_tool_names.append(tool_name)

    def _get_available_commands(self) -> dict[str, dict]:
        """Discover and return available commands dynamically.

        Queries the command discovery system to retrieve all available commands
        (both built-in and custom commands from the commands/ directory).
        Results are not cached to ensure fresh command list on each call.

        Returns:
            dict: Mapping of command names to their command metadata dictionaries.
                Each command dict contains keys like 'name', 'description', etc.
        """
        return {c["name"]: c for c in discover_commands()}

    def _handle_command(
        self, cmd_name: str, cmd_full: str, msg: Optional[InputMessage] = None
    ) -> None:
        """Dispatch a slash command to the appropriate handler.

        Delegates to CommandManager which resolves priority (PY > BUILTIN > MD)
        and dispatches to the correct source.
        """
        if cmd_full in ("", "/"):
            show_help()
            return

        if CommandManager.dispatch(cmd_name, cmd_full, msg, self):
            return

        unknown_command_error(cmd_name)

    def resolve_group_params(self) -> dict[str, Any]:
        """Return generation parameters for the current LLM group.

        Resolves generation parameters following a priority order from lowest to highest:
        1. Global inference_params (deprecated fallback from top-level "inference" block)
        2. Group-specific generation params (max_tokens, temperature, top_p, etc.)
        3. Group-specific chat_template_kwargs

        Returns:
            dict: Merged generation parameters with group-specific values taking
                precedence over global defaults.
        """
        group = self.llm_groups[self.current_group_name]
        params: dict[str, Any] = {}

        # 1) Global inference_params as base (deprecated fallback for configs
        #    that still use the top-level "inference" block).
        if self.inference_params:
            params.update(self.inference_params)

        # 2) Group-specific generation params override global values.
        for attr in (
            "max_tokens",
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "presence_penalty",
            "frequency_penalty",
            "repetition_penalty",
        ):
            val = getattr(group, attr, None)
            if val is not None:
                params[attr] = val

        # 3) Group-specific chat_template_kwargs override global values.
        if group.chat_template_kwargs:
            params["chat_template_kwargs"] = group.chat_template_kwargs

        return params

    def _rebuild_client(self, clear_overrides: bool = False) -> None:
        """Rebuild the HTTP client and refresh parameters after LLM group switch.

        Reinitializes the SimpleOpenAIClient with the current LLM group's
        configuration. Optionally clears any model/base URL/context overrides.

        Args:
            clear_overrides: If True, clears all LLM configuration overrides
                (_llm_model_override, _llm_base_url_override, _llm_context_override).
                If False, preserves existing overrides.

        Raises:
            ValueError: If the current LLM group is not found.
        """
        group = self.llm_groups.get(self.current_group_name)
        if not group:
            raise ValueError(
                f"No LLM group '{self.current_group_name}' found. "
                f"Available groups: {list(self.llm_groups.keys()) or '(none)'}"
            )
        if clear_overrides:
            self._llm_model_override = None
            self._llm_base_url_override = None
            self._llm_context_override = None
        self._current_api_key = group.api_key
        self.client = SimpleOpenAIClient(
            base_url=self._llm_base_url_override or group.api_base,
            api_key=self._current_api_key,
            timeout=group.timeout,
            cache_tracker=self._cache_tracker,
        )
        self.model_name = self._llm_model_override or group.model
        self.base_url = self._llm_base_url_override or group.api_base
        self.max_tokens = group.max_tokens
        self.max_context_tokens = (
            self._llm_context_override
            if self._llm_context_override is not None
            else group.max_context_tokens
        )

        # Swap reflection scheduler if the new group has its own reflection config
        if group.reflection is not None:
            self.reflection_scheduler = ReflectionScheduler(group.reflection)
            self._loop_escalation.set_reflection_scheduler(self.reflection_scheduler)

    def get_all_tools(self) -> list[dict]:
        """Return filtered tools in OpenAI function-calling format.

        Retrieves all available tools, applies the tool filter, and converts
        them to the OpenAI function-calling schema format.

        Returns:
            list[dict]: List of tool definitions in OpenAI format, each containing:
                - type: Always "function"
                - function: Dict with name, description, and parameters schema
        """
        all_tools = []
        for name in self.available_tool_names:
            if not self.tool_filter.should_include(name):
                continue
            tool_info = TOOLS.get(name)
            if not tool_info:
                continue
            schema = tool_info.get_schema()
            all_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool_info.description,
                        "parameters": schema
                        or {"type": "object", "properties": {}, "required": []},
                    },
                }
            )
        return all_tools

    def invoke_with_tools(self, user_input: str) -> str:
        """Send *user_input* to the model, execute any tool calls, return response text.

        CRITICAL OPENAI COMPLIANCE:
        This method APPENDS a user message to the context before calling the LLM.
        The context MUST end with an assistant or tool message before this call
        to maintain valid message alternation (no consecutive user messages).

        If the context already ends with a user message, use invoke_with_tools_loop()
        directly instead to avoid violating the OpenAI spec.

        Args:
            user_input: The user message content to append and send to the model.

        Returns:
            The final assistant response text (or error message).
        """
        self._session.audit_writer.user(user_input)
        self.context.append_user(user_input)
        result = self.invoke_with_tools_loop()
        self._session.audit_writer.flush()
        return result

    def invoke_with_tools_loop(self) -> str:
        """Core loop: call LLM, execute tool calls, repeat until final response.

        CRITICAL OPENAI COMPLIANCE REQUIREMENT:
        The context MUST already end with a user message when this method is called.
        This method does NOT append any messages before the LLM call.

        Message alternation invariant maintained throughout:
          - After LLM returns tool_calls: assistant(tool_calls) -> tool results -> loop
          - After LLM returns plain text: assistant(response) -> recovery reminder -> loop
          - After end_turn tool call: assistant(force_end_turn) -> turn complete
          - After recovery: assistant(response) -> user(correction) -> continue

        This method is safe to call when the context ends with:
          - A user message (normal entry point)
          - A tool message (after tool execution, looping back to LLM)

        Do NOT call this if the context ends with an assistant message, as that
        would indicate an incomplete turn that should be closed first.

        Returns:
            The final assistant response text (or error message).
        """
        self.loop_detector.reset()
        outer_recovery_counter = 0
        max_outer_recovery = 5

        self.force_end_turn: str | None = None
        self.last_substantive_response: str | None = None
        self._recovery_active = False  # Reset recovery mode at start of each turn

        # Early entry reflection (microplan) before first LLM call
        _early_reflection_done = False
        if (
            self.reflection_scheduler.cfg.enabled
            and self.reflection_scheduler.cfg.initial_think
        ):
            self._loop_escalation.inject_early_reflection()
            _early_reflection_done = True

        while True:
            if AgentLifecycle.is_exit_requested():
                self.context.close_turn("[Session ended]")
                break

            if AgentLifecycle.is_interrupted():
                self.context.close_turn("[Interrupted]")
                break

            all_tools = self.get_all_tools()

            errors = self.context.validate()
            if errors:
                last = self.context[-1] if self.context else None
                last_role = last.get("role", "empty") if last else "empty"
                context_validation_display(errors, context_len=len(self.context),
                                           last_role=last_role)
                recovered, fixes = self.context.attempt_recovery()
                if fixes:
                    context_recovery_display(fixes, recovered)
                    if not recovered:
                        log_console_warning(
                            f"Context recovery incomplete: {len(self.context.validate())} "
                            "errors remain after recovery attempt"
                        )

            try:
                extra_kwargs = self.resolve_group_params()

                config = LLMCallConfig(
                    log_on_failure=True,
                    log_file=self._session.audit_file,
                    context=self.context,
                    extra_kwargs=extra_kwargs,
                    compress_client=self.client,
                    compress_model=self.model_name,
                    compress_tools=all_tools,
                    compress_extra_kwargs=extra_kwargs,
                    compress_audit_writer=self._session.audit_writer,
                )
                resp, compressed = _invoke_llm_with_retry(
                    self.client,
                    self.model_name,
                    self.context,
                    all_tools,
                    "auto",
                    stream=False,
                    config=config,
                    valid_tool_names=set(self.available_tool_names),
                )
                response_text = resp.text
                reasoning_content = resp.reasoning
                call_stats = resp.stats

                # Record content quality for adaptive interval
                self.reflection_scheduler.record_llm_response(
                    assistant_bytes=len(response_text or ""),
                    reasoning_bytes=len(reasoning_content or ""),
                )

                # Persist compressed context back to agent context.
                if compressed is not None:
                    self.context.set_messages(compressed)

                # Transform resp.tool_calls (SDK + postparse-recovered) into executor format.
                # resp.tool_calls is the authoritative list — do NOT re-read from
                # the raw SDK response, which would miss postparse-extracted calls.
                # NOTE: Tool calls are processed even for best-effort responses.
                #  Previously, validation failures would skip tool execution.
                tool_calls = []
                for tc in resp.tool_calls:
                    args_str = tc["function"]["arguments"] or ""
                    try:
                        args_dict = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        args_dict = {}
                    tool_calls.append(
                        {
                            "id": tc["id"],
                            "name": tc["function"]["name"],
                            "args": args_str,
                            "args_dict": args_dict,
                        }
                    )

                # Token fields may be None when the API does not report usage.
                # Use ``or 0`` for arithmetic; store raw (possibly None) for display.
                self._session.record_call_stats(call_stats)

                print_context_status(self.get_status())

                if reasoning_content:
                    reasoning(reasoning_content.strip())
                if response_text:
                    assistant_message_display(response_text.strip())

                # Compression check: use API tokens when available, fall back to
                # estimation (including pending message) when not.  This MUST happen
                # BEFORE appending the assistant message, so we estimate the
                # post-append context size to avoid lagging estimates.
                pending = (
                    len(response_text or "") + len(reasoning_content or "")
                ) // 3 + 15
                if (
                    self._session.last_exact_context_tokens is not None
                    and self._session.last_exact_context_tokens > 0
                ):
                    total_tokens = self._session.last_exact_context_tokens + pending
                else:
                    total_tokens = self.context.estimate_tokens(pending)
                compress_threshold = 0.85

                if total_tokens / self.max_context_tokens >= compress_threshold:
                    self.context.compress(0.30, self, self.get_all_tools())

                # Track substantive response for potential ENDTURN resolution.
                # Track ALL responses with text (even with tool calls) so ENDTURN
                # resolves correctly. Never overwrite existing during recovery mode.
                if response_text and response_text.strip() and (self.last_substantive_response is None or not self._recovery_active):
                    self.last_substantive_response = response_text.strip()

                if tool_calls:
                    # Validate: end_turn must be the sole tool call.
                    # If mixed with other tools, execute them but reject end_turn.
                    has_end_turn = any(tc["name"] == "end_turn" for tc in tool_calls)
                    if has_end_turn and len(tool_calls) > 1:
                        # Separate end_turn from other tool calls
                        other_tool_calls = [tc for tc in tool_calls if tc["name"] != "end_turn"]
                        
                        # Execute the other tool calls
                        if other_tool_calls:
                            execute_tool_batch(other_tool_calls, self, reasoning=reasoning_content, audit_writer=self._session.audit_writer)
                        
                        # Reject end_turn with concise reminder
                        warning(
                            "end_turn must be called alone. "
                            "Finish your work, then call end_turn by itself."
                        )
                        self.context.append_synthetic_user(
                            "end_turn_rejection",
                            "end_turn must be called alone. Finish your work, then call end_turn by itself.",
                        )
                        continue

                    outer_recovery_counter = 0
                    execute_tool_batch(tool_calls, self, reasoning=reasoning_content, audit_writer=self._session.audit_writer)

                    # Count this as a tool-loop step
                    self.reflection_scheduler.tick()

                    info = self.loop_detector.get_escalation_info()
                    # Signal distress so scheduler narrows interval
                    if info["escalation_level"] >= 1 or self._session.has_error_burst():
                        self.reflection_scheduler.on_distress()
                    # Check reflection — periodic OR reactive
                    if self.reflection_scheduler.should_reflect():
                        self.reflection_scheduler.mark_reflection_started()
                        self._loop_escalation.inject_reflection()
                        continue
                    if self.reflection_scheduler.should_reflect_reactive(
                        has_loop_warning=info["escalation_level"] >= 1,
                        has_error_burst=self._session.has_error_burst(),
                    ):
                        self.reflection_scheduler.mark_reflection_started()
                        self._loop_escalation.inject_reflection()
                        continue

                    # (tool_filter is never changed at runtime — prefix cache safety)

                    # Check for forced end-of-turn (set by end_turn or any tool)
                    if self.force_end_turn is not None:
                        # close_turn() handles synthetic message cleanup automatically
                        self.context.close_turn(self.force_end_turn)
                        return self.force_end_turn

                    # Check for interrupt after tool execution
                    if AgentLifecycle.is_interrupted():
                        self.context.close_turn("[Interrupted]")
                        break

                    errors = self.context.validate_tool_resolution()
                    if errors:
                        for err in errors:
                            warning(f"Tool resolution warning: {err}")

                    # Check for loop escalation after tool batch
                    if not self._loop_escalation.handle_loop_escalation():
                        if self.force_end_turn is not None:
                            # close_turn() handles synthetic message cleanup automatically
                            self.context.close_turn(self.force_end_turn)
                            return self.force_end_turn

                    continue

                # ── No tool calls: validate structure, then require end_turn ──
                # Plain text responses NEVER end the turn. The model MUST call the
                # end_turn tool to complete the turn. Here we check for structural
                # errors (truncation, unclosed tags, malformed tool calls) and then
                # inject a recovery reminder forcing the model to call end_turn.

                # Check for structural errors (truncation, unclosed tags, malformed tool calls)
                validation_error = is_valid_end_of_turn(
                    response_text, call_stats.finish_reason, reasoning_content
                )
                if validation_error is not None:
                    if validation_error.error_type == ValidationErrorType.TRUNCATED:
                        warning("output truncated (finish_reason=length, hit max_tokens)")
                    outer_recovery_counter += 1
                    if outer_recovery_counter >= max_outer_recovery:
                        warning(
                            f"[Recovery budget exhausted ({max_outer_recovery} attempts), "
                            f"returning best-effort response.]"
                        )
                        self.context.close_turn(
                            "[Recovery budget exhausted — turn forced closed]"
                        )
                        best_response = self.last_substantive_response or response_text or ""
                        self._session.audit_writer.assistant(best_response)
                        return best_response
                    self._loop_escalation.recover_from_invalid_end_of_turn(
                        response_text,
                        reasoning_content,
                    )
                    continue

                # ── Plain text without end_turn: inject recovery, continue ──
                # The model must explicitly call end_turn to end the turn.
                outer_recovery_counter += 1
                if outer_recovery_counter >= max_outer_recovery:
                    warning(
                        f"[Recovery budget exhausted ({max_outer_recovery} attempts), "
                        f"returning best-effort response.]"
                    )
                    self.context.close_turn(
                        "[Recovery budget exhausted — turn forced closed]"
                    )
                    best_response = self.last_substantive_response or response_text or ""
                    self._session.audit_writer.assistant(best_response)
                    return best_response

                # Inject recovery reminder and continue loop
                self._recover_from_missing_end_turn(response_text, reasoning_content)
                continue

            except Exception as e:  # pylint: disable=W0718
                traceback.print_exc()

                error_detail = f"{type(e).__name__}: {e}"
                error_lower = error_detail.lower()
                if "timeout" in error_lower or "timed out" in error_lower:
                    error_response = (
                        f"Error: Failed to invoke model after retries - {error_detail}"
                    )
                else:
                    error_response = f"Error: Failed to invoke model - {error_detail}"

                self._session.audit_writer.assistant(error_response)
                self.context.append_assistant(error_response, None)
                error(f"[ERROR] {error_response}")
                return error_response

    def _recover_from_missing_end_turn(
        self,
        response_text: str | None,
        reasoning_content: str | None,
    ) -> None:
        """Inject recovery message when model returns plain text without end_turn.

        Preserves the model's response (and reasoning) as an assistant message,
        then injects a synthetic user reminder to call end_turn.

        Context alternation: assistant (plain text + reasoning) → synthetic user → LLM.
        Since undo() skips synthetic messages, the undo boundary is preserved.

        Sets _recovery_active to prevent overwriting last_substantive_response
        during the recovery round.
        """
        # Append the model's response (with reasoning) to preserve its work
        if response_text is not None:
            self.context.append_assistant(response_text, reasoning=reasoning_content)

        # Lock last_substantive_response — recovery responses should not overwrite it
        self._recovery_active = True

        # Build the reminder with context about what ENDTURN will resolve to
        preview = ""
        if self.last_substantive_response:
            preview_text = self.last_substantive_response[:40].rstrip()
            preview = f' Your last response started with: "{preview_text}..." '

        reminder = (
            "You must call the end_turn tool to end your turn. "
            "If your answer is already above, pass 'ENDTURN' as the message. "
            "Otherwise, provide your final response as the message."
        )

        # Get the last real user prompt to provide context
        last_real_prompt = get_last_real_user_prompt(self.context.get_messages())

        # Build the synthetic user message content
        synthetic_content = f"{reminder}\n\nOriginal prompt for this turn:\n{last_real_prompt}"

        # Append the synthetic user message via public method
        self.context.append_synthetic_user("end_turn_reminder", synthetic_content)

    def run(
        self,
        inputs: list[str] = None,
        a2a_server=None,
        keep_alive=False,
        interactive=True,
    ) -> None:
        """Delegate the main run loop to InputHandler.

        Starts the agent's main execution loop by delegating to the InputHandler
        which manages user input processing, command handling, and interaction flow.

        Args:
            inputs: Optional list of initial input strings to process.
            a2a_server: Optional A2A (Agent-to-Agent) server instance.
            keep_alive: If True, keep the agent running after processing inputs.
            interactive: If True, enable interactive mode for user input.
        """
        InputHandler(self).run(
            inputs=inputs,
            a2a_server=a2a_server,
            keep_alive=keep_alive,
            interactive=interactive,
        )

    def _exec_tool(self, args: str) -> None:
        """Execute a tool directly from a command line.

        Parses and executes a tool with the given arguments in key=value format.
        This is used by the /exec command to run tools interactively.

        Args:
            args: Space-separated arguments in the format:
                "toolname arg1=val1 arg2=val2 ..."
                Example: "cd path=.." or "search query=python"

        Displays:
            - Usage message if no arguments provided
            - Error if tool not found
            - Error if tool has no run function
            - Tool result or error message
        """
        if not args:
            exec_usage()
            return

        parts = args.split()
        if not parts:
            exec_usage()
            return

        tool_name = parts[0]
        tool_args = {}
        for part in parts[1:]:
            if "=" in part:
                key, value = part.split("=", 1)
                if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
                    value = int(value)
                elif value.replace(".", "").replace("-", "").isdigit():
                    try:
                        value = float(value)
                    except ValueError:
                        # Keep original string if float conversion fails
                        pass
                tool_args[key] = value

        tool_info = TOOLS.get(tool_name)
        if not tool_info:
            unknown_tool_error(tool_name)
            return
        tool_func = tool_info.run
        if not tool_func:
            no_run_function_error(tool_name)
            return

        tool_args["agent"] = self
        tool_args["tool_call_id"] = "0"

        # Fill optional parameter defaults from the tool's Args dataclass.
        # This must happen AFTER tool resolution so that alias-resolved canonical
        # argument names match the dataclass field names.
        tool_module = tool_info.module
        if tool_module is not None and hasattr(tool_module, "Args"):
            from tools.validation import fill_defaults_from_args
            fill_defaults_from_args(tool_args, tool_module)

        try:
            result = tool_func(**tool_args) if tool_args else tool_func()
            assistant_message_display(result)
        except (TypeError, KeyError, RuntimeError) as e:
            exec_tool_fail(str(e))

    def clear_context(self) -> str:
        """Clear all messages except the system prompt and reset token counters.

        Removes all conversation messages from the context while preserving
        the system prompt. Resets all token counters and cache tracker.

        Returns:
            str: Confirmation message "Context cleared."
        """
        self.context.clear()
        self._session.clear_tokens()
        return "Context cleared."

    @_command("undo", "u")
    def _undo_last(
        self, cmd_full: str = "", msg: Optional[InputMessage] = None
    ) -> None:
        """Undo the last conversation turn.

        Removes messages from the last user message onward, effectively
        reverting the last turn. This allows correcting mistakes or trying
        a different approach.

        Displays the number of messages removed via the console.
        """
        old_len = len(self.context)
        self.context.undo()
        undo_message(old_len - len(self.context))

    def _load_context_by_id(self, idx: int) -> dict | None:
        """Load a context file by its ID from the context list.

        Retrieves a context file entry from the list of available contexts
        using a 1-based index.

        Args:
            idx: 1-based index of the context to load.

        Returns:
            dict | None: The context dictionary containing 'name' and 'file' keys
                if the ID is valid, otherwise None.

        Displays:
            - Error message if ID is out of range
        """
        from agent_input import list_context_files

        contexts = list_context_files()
        if not contexts or idx < 1 or idx > len(contexts):
            echo(f"ID {idx} out of range (1-{len(contexts)})")
            return None
        return contexts[idx - 1]

    def _copy_plan_file(self, old_context_file: Path) -> None:
        """Copy the old session's .plan file to the new session's plan path.

        Called after /continue loads a context from a previous session so that
        plan entries survive session restoration.
        """
        old_plan = old_context_file.with_suffix(".plan")
        if not old_plan.exists():
            return

        if not SESSION_PREFIX:
            return

        new_plan = LOG_DIR / f"{SESSION_PREFIX}.plan"
        if old_plan != new_plan:
            try:
                shutil.copy2(old_plan, new_plan)
            except OSError as e:
                warning(f"Failed to copy plan file {old_plan} -> {new_plan}: {e}")

    def _copy_audit_file(self, old_context_file: Path) -> None:
        """Copy the old session's .audit file into the current session's audit file.

        Called after /continue loads a context from a previous session so that
        /audit shows the full history (old + new session records).

        Appends old audit content to the current audit file so the audit writer
        can continue writing to the same file without losing history.
        """
        old_audit = old_context_file.with_suffix(".audit")
        if not old_audit.exists():
            return

        new_audit = self._session.audit_file
        if old_audit != new_audit:
            try:
                with open(old_audit, "r", encoding="utf-8") as src:
                    content = src.read()
                with open(new_audit, "a", encoding="utf-8") as dst:
                    dst.write(content)
            except OSError as e:
                warning(f"Failed to copy audit file {old_audit} -> {new_audit}: {e}")

    def _handle_continue(self, args: str) -> None:
        """Handle the /continue command to load previous contexts.

        Supports multiple subcommands for loading and previewing saved contexts:
        - No arguments: Load the latest context from the same terminal session
        - "list": List saved contexts (default 25, or specify count)
        - "<n>": Load context by ID
        - "preview <n>": Preview the last 3 messages of context by ID

        Args:
            args: Command arguments. Examples:
                - "" (empty) - Load latest context
                - "list" - List contexts
                - "list 50" - List last 50 contexts
                - "5" - Load context #5
                - "preview 5" - Preview context #5

        Displays:
            - Context restoration success/failure messages
            - List of contexts for "list" subcommand
            - Preview of context for "preview" subcommand
            - Usage help for invalid arguments
        """
        from agent_input import (
            get_context_file_by_parent_ppid,
            list_context_files,
            preview_context,
        )

        if not args:
            target_ctx = get_context_file_by_parent_ppid()
            if target_ctx:
                self._session.context_file = target_ctx
                if self.context.load_from_file(self._session.context_file):
                    self._copy_plan_file(self._session.context_file)
                    self._copy_audit_file(self._session.context_file)
                    context_restored(len(self.context), target_ctx)
                else:
                    context_restore_failure(target_ctx)
            else:
                no_context_file_found()
            return

        parts = args.split(maxsplit=1)
        sub_cmd = parts[0].lower()

        if sub_cmd == "list":
            n_str = (parts[1].strip() if len(parts) > 1 else "").strip()
            n = int(n_str) if n_str.isdigit() and int(n_str) > 0 else 25
            if n_str and not n_str.isdigit():
                echo("Usage: /continue list [<n>]")
                return
            context_list_display(list_context_files(limit=n))

        elif sub_cmd == "preview":
            if len(parts) < 2 or not parts[1].strip():
                echo("Usage: /continue preview <n>")
                return
            try:
                idx = int(parts[1].strip())
            except ValueError:
                echo(f"Invalid ID: {parts[1].strip()}")
                return
            ctx = self._load_context_by_id(idx)
            if ctx is None:
                return
            context_preview_display(
                ctx["name"], preview_context(ctx["file"])
            )

        else:
            try:
                idx = int(args.strip())
            except ValueError:
                echo(
                    f"Unknown /continue argument: {args}\nUsage: /continue | /continue list [<n>] | /continue <n> | /continue preview <n>"
                )
                return
            ctx = self._load_context_by_id(idx)
            if ctx is None:
                return
            self._session.context_file = ctx["file"]
            if self.context.load_from_file(self._session.context_file):
                self._copy_plan_file(self._session.context_file)
                self._copy_audit_file(self._session.context_file)
                context_restored(len(self.context), self._session.context_file)
            else:
                context_restore_failure(self._session.context_file)

    def get_status(self) -> AgentStatus:
        """Return an encapsulated view of agent status for display functions.

        Replaces direct access to agent internals from the display layer.
        """
        token_count, percentage, byte_count, is_exact = self.context.get_usage_stats(
            self.max_context_tokens, self._session.last_exact_context_tokens
        )

        pending = None
        if self.context.is_tool_pending():
            pending = self.context.get_pending_tool_ids()

        return AgentStatus(
            # Context stats
            token_count=token_count,
            percentage=percentage,
            byte_count=byte_count,
            is_exact=is_exact,
            context_len=len(self.context),
            max_context_tokens=self.max_context_tokens,
            # Pending tools
            pending_tool_ids=pending,
            # Model info
            model_name=self.model_name,
            base_url=self.base_url,
            model_source="cli" if self._llm_model_override else f"group:{self.current_group_name}",
            base_url_source="cli" if self._llm_base_url_override else f"group:{self.current_group_name}",
            # Group info
            current_group_name=self.current_group_name,
            llm_groups=list(self.llm_groups.keys()),
            gen_params=self.resolve_group_params(),
            # Token tracking
            last_turn_in=self._session.last_turn_input_tokens,
            last_turn_out=self._session.last_turn_output_tokens,
            last_turn_cached=self._session.last_turn_cached_tokens,
            session_in=self._session.input_tokens,
            session_out=self._session.output_tokens,
            session_cached=self._session.cached_tokens,
            # Cache
            has_cache_data=self._session.cache_tracker.has_cache_data,
            cumulative_hit_rate=self._session.cache_tracker.cumulative_hit_rate,
            sliding_hit_rate=self._session.cache_tracker.sliding_hit_rate,
            last_hit_rate=self._session.cache_tracker.last_hit_rate,
            call_count=self._session.cache_tracker.call_count,
            # Agent info
            agent_name=self.agent_name,
            context_file=str(self._session.context_file),
            nesting_count=self.nesting_count,
            # Loop detection
            loop_stats=self.loop_detector.get_stats(),
            # Commands
            available_commands=list(self._get_available_commands().keys()),
        )

    def _handle_restart(self, restart_args: str) -> None:
        """Restart the agent with the same configuration.

        Restarts the agent process while preserving the current context and
        configuration. Filters out irrelevant flags and ensures the -c flag
        is set to continue from the saved context.

        Args:
            restart_args: Additional arguments to pass to the restarted agent.

        Displays:
            - Restart command being executed
            - Error messages if restart fails

        Actions:
            - Saves current context to file
            - Clears bytecode cache
            - Attempts execvp for clean restart
            - Falls back to subprocess.Popen if execvp fails
        """
        self._session.clear_tokens()  # Clear stale cache stats from previous session
        skip_flags = {
            "--pid",
            "--card",
            "--timeout",
            "--list",
            "--list-all",
            "--listjson",
            "--listjson-all",
            "--query",
        }

        filtered_args = []
        i = 0
        while i < len(sys.argv[1:]):
            arg = sys.argv[i + 1]  # +1 because sys.argv[0] is script name
            if arg in skip_flags:
                i += 2
                continue
            if not arg.startswith("-"):
                i += 1
                continue
            filtered_args.append(arg)
            if (
                "=" not in arg
                and i + 2 < len(sys.argv)
                and not sys.argv[i + 2].startswith("-")
            ):
                filtered_args.append(sys.argv[i + 2])
                i += 2
            else:
                i += 1

        if not any(arg in ("-c", "--continue") for arg in filtered_args):
            filtered_args.append("-c")

        cmd = [sys.executable, sys.argv[0]] + filtered_args
        if restart_args:
            cmd.extend(restart_args.split())

        restart_flow(" ".join(cmd))
        print_agent_exit_summary(self)
        self.context.close_turn("[Restart]")
        self.context.save_to_file(self._session.context_file, force=True)
        time.sleep(0.1)

        # Clear bytecode cache
        import shutil

        for cache_dir in ("tools/__pycache__", "__pycache__"):
            path = Path(__file__).parent / cache_dir
            if path.exists():
                shutil.rmtree(path)

        try:
            os.execvp(cmd[0], cmd)
        except OSError as e:
            restart_failure(str(e))
            try:
                subprocess.Popen(cmd)
            except OSError as e2:
                restart_fallback_failure(str(e2))
        sys.exit(0)

    # ── Backward-compatible property wrappers (tests access these directly) ──

    @property
    def audit_file(self) -> Path:
        """Delegate to session manager."""
        return self._session.audit_file

    @property
    def context_file(self) -> Path:
        """Delegate to session manager."""
        return self._session.context_file

    @context_file.setter
    def context_file(self, value: Path) -> None:
        self._session.context_file = value

    @property
    def audit_writer(self) -> AuditWriter:
        """Delegate to session manager."""
        return self._session.audit_writer
