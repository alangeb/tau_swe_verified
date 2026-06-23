"""Delegate command — orchestrator mode for task delegation."""

from agent_console import error
from agent_console_primitives import blank_line, echo, status
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Metadata ──

NAME = "delegate"
DESCRIPTION = "Enter orchestrator mode (plan & delegate via fork/subagent)"
subcommands = ()
help_text = "Usage: /delegate <task description>"

DELEGATE_INSTRUCTIONS = (
    "You are in DELEGATE MODE. You are an orchestrator — you plan and delegate, "
    "you do NOT do work yourself.\n"
    "\n"
    "Rules:\n"
    "  1. Break the task into subtasks and delegate each via `fork` (needs full "
    "context) or `subagent` (isolated, blank-slate).\n"
    "  2. NEVER do work yourself — no file edits, no shell commands, no writes.\n"
    "  3. Your children (fork/subagent) have FULL tool access. You do not.\n"
    "  4. When all subtasks are done or abandoned: call `end_turn` with a comprehensive summary.\n"
    "  5. Use read/analysis tools (glob, file_read, pyscan, grep) to understand "
    "the codebase before delegating.\n"
    "  6. There is no iteration limit — track progress yourself and call "
    "`end_turn` when done.\n"
    "\n"
    "Begin delegating."
)


# ── Execution ──

def run(agent: "TauBot", args: list[str]) -> None:
    if not args or args[0] in ("help", "-h", "h"):
        echo("Usage: /delegate <task description>")
        return

    task = " ".join(args)
    blank_line()
    status(f"[DELEGATE MODE] Task: {task}")
    blank_line()

    # NOTE: We do NOT change tool_filter — that would break prefix caching.
    # Tool restrictions are enforced via LLM instructions only.
    agent.force_end_turn = None
    prompt = f"TASK: {task}\n\n{DELEGATE_INSTRUCTIONS}"
    agent.invoke_with_tools(prompt)
    while agent.force_end_turn is None:
        agent.invoke_with_tools(f"Continue TASK.\n\n{DELEGATE_INSTRUCTIONS}")
    agent.force_end_turn = None
