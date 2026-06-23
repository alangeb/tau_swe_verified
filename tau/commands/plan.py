"""Plan command — direct interface to the plan tool."""

from agent_console import error
from agent_console_primitives import echo
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauBot

# ── Metadata ──
NAME = "plan"
DESCRIPTION = "Manage tasks: status, clear, create, add, complete, block, unblock"
ALIASES_CMD = ["task"]
subcommands = ("status", "clear", "create", "add", "complete", "block", "unblock", "next")
help_text = """Usage: /plan <action> [args...]
Actions:
  status          Show plan status
  clear           Clear all tasks
  create          Create a new plan
  add <desc> [pri] Add a task (priority=low|medium|high)
  complete <id>   Mark task complete
  block <id> [reason] Block a task
  unblock <id>    Unblock a task
  next            Show next actionable task"""

_VALID_ACTIONS = {"status", "clear", "create", "add", "complete", "block", "unblock"}


# ── Execution ──
def run(agent: "TauBot", args: list[str]) -> None:
    if not args or args[0] in ("help", "-h", "h"):
        echo(
            "Usage: /plan <action> [args...]\n"
            "Actions:\n"
            "  status\n"
            "  clear\n"
            "  create\n"
            "  add <description> [priority=low|medium|high]\n"
            "  complete <task_id> [notes=...]\n"
            "  block <task_id> [blocker_reason=...]\n"
            "  unblock <task_id>\n"
            "  next"
        )
        return

    action = args[0].lower()
    if action not in _VALID_ACTIONS:
        error(
            f"Unknown action: {action}. Valid: {', '.join(sorted(_VALID_ACTIONS))}"
        )
        return

    kwargs: dict[str, object] = {"action": action}

    positional: list[str] = []
    for arg in args[1:]:
        if "=" in arg:
            key, value = arg.split("=", 1)
            kwargs[key] = value
        else:
            positional.append(arg)

    if positional:
        if action == "add" and "description" not in kwargs:
            kwargs["description"] = " ".join(positional)
        elif "task_id" not in kwargs:
            kwargs["task_id"] = positional[0]

    from tools.plan import run as plan_run

    result = plan_run(agent=agent, tool_call_id=None, **kwargs)
    if result:
        echo(result)
