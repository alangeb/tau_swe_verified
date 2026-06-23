"""Tool filtering for TauBot.

Provides tool filtering functionality via allowlist or blocklist,
supporting wildcard patterns via fnmatch for flexible matching.

Example
    from agent_tool_filter import ToolFilter
    tf = ToolFilter(allowlist={"read", "grep"})
    tf.should_include("read")    # True
    tf.should_include("bash")    # False
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Optional


@dataclass
class ToolFilter:
    """Filter tools by allowlist (priority) or blocklist with wildcard support.

    Provides tool filtering functionality for the TauBot, allowing either
    an allowlist or blocklist of tool names. Supports wildcard patterns via
    fnmatch for flexible matching.

    Attributes:
        allowlist: Optional set of allowed tool names or patterns. If set,
            only matching tools are included.
        blocklist: Optional set of blocked tool names or patterns. If set,
            matching tools are excluded.
        use_wildcards: If True, treat patterns as fnmatch wildcards (e.g., "file_*").
        denied_message: Optional custom message template for blocked tools.
            Supports {tool_name} and {available_tools} placeholders.
    """

    allowlist: Optional[set[str]] = None
    blocklist: Optional[set[str]] = None
    use_wildcards: bool = True
    denied_message: Optional[str] = None

    def should_include(self, tool_name: str) -> bool:
        """Return True if *tool_name* passes the filter."""
        if self.allowlist:
            return any(
                (
                    fnmatch.fnmatch(tool_name, pat)
                    if self.use_wildcards
                    else tool_name == pat
                )
                for pat in self.allowlist
            )
        if self.blocklist:
            return not any(
                (
                    fnmatch.fnmatch(tool_name, pat)
                    if self.use_wildcards
                    else tool_name == pat
                )
                for pat in self.blocklist
            )
        return True

    def format_denied(self, tool_name: str, available_tools: list[str]) -> str:
        """Format the denial message for a blocked tool invocation.

        Args:
            tool_name: The blocked tool name.
            available_tools: List of tool names the agent MAY use (any order; output is sorted).

        Returns:
            Formatted denial string ready for injection into LLM context.
        """
        tools_str = ", ".join(sorted(available_tools))
        if self.denied_message is not None:
            try:
                return self.denied_message.format(
                    tool_name=tool_name,
                    available_tools=tools_str,
                )
            except (KeyError, ValueError):
                pass  # Malformed template — fall through to default

        # Default: concise, instructive, tells LLM what to do instead
        return (
            f"Tool '{tool_name}' is restricted. "
            f"Available tools: {tools_str}. "
            f"Reformulate your approach using only the available tools."
        )

    def get_available(self, all_tool_names: list[str]) -> list[str]:
        """Return the list of tool names that pass this filter.

        Args:
            all_tool_names: All tool names the agent has access to.

        Returns:
            Filtered list of tool names sorted alphabetically.
        """
        return sorted(n for n in all_tool_names if self.should_include(n))
