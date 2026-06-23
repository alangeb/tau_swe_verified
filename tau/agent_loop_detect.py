"""Loop detection for TauBot tool call sequences.

Detects repetitive and cyclical patterns indicating potential infinite loops:
1. Consecutive repeat detection: warns after repeat_threshold identical calls (default: 3)
2. Shannon entropy analysis: warns when entropy drops below 1.5 over rolling window (default: 30)

Key class:
- LoopDetector: Main detection class with configurable window_size and repeat_threshold

Escalation levels:
- Level 0: No escalation (normal operation)
- Level 1: Escalating text warnings in tool output
- Level 2: Context injection suggesting think tool
- Level 3: Forced think via tool filter
- Level 4: force_end_turn (nuclear option)
"""

import json
import math
from collections import Counter, deque

# Escalating warning message templates
WARNING_LEVEL_1 = (
    "⚠️ LOOP WARNING #{warning_count}: Tool '{tool_name}' called {consecutive} times consecutively. "
    "This is warning #{warning_count} this turn. Consider changing your approach."
)

WARNING_LEVEL_2 = (
    "🔴 LOOP WARNING #{warning_count}: Tool '{tool_name}' called {consecutive} times consecutively. "
    "This is warning #{warning_count} this turn. You appear stuck in a loop. "
    "Use the 'think' tool to re-analyze the situation before continuing."
)

WARNING_LEVEL_3 = (
    "🚨 CRITICAL LOOP #{warning_count}: Tool '{tool_name}' repeated {consecutive} times. "
    "Warning #{warning_count} this turn. You MUST use the 'think' tool now. "
    "Do not call any other tools until you have analyzed and planned."
)

# Entropy warning templates (parallel to WARNING_LEVEL_*)
ENTROPY_WARNING_LEVEL_1 = (
    "⚠️ LOW ENTROPY #{warning_count}: Tool '{tool_name}' pattern "
    "highly predictable (entropy: {entropy:.2f}). "
    "Consider using 'think' to re-analyze."
)

ENTROPY_WARNING_LEVEL_2 = (
    "🔴 ENTROPY WARNING #{warning_count}: Tool '{tool_name}' pattern "
    "highly predictable (entropy: {entropy:.2f}). "
    "Use the 'think' tool to re-analyze the situation."
)

ENTROPY_WARNING_LEVEL_3 = (
    "🚨 CRITICAL ENTROPY #{warning_count}: Tool '{tool_name}' pattern "
    "highly predictable (entropy: {entropy:.2f}). "
    "You MUST use the 'think' tool now."
)

# Grouped templates: index = escalation_level-1 (clamped to 0..2)
_WARNING_TEMPLATES = [WARNING_LEVEL_1, WARNING_LEVEL_2, WARNING_LEVEL_3]
_ENTROPY_TEMPLATES = [ENTROPY_WARNING_LEVEL_1, ENTROPY_WARNING_LEVEL_2, ENTROPY_WARNING_LEVEL_3]

__all__ = ["LoopDetector"]


class LoopDetector:
    """Detect repetitive or cyclical tool-call patterns.

    1. **Repeat**: Warns after *repeat_threshold* identical consecutive calls.
    2. **Entropy**: Warns when Shannon entropy over the last *window_size* calls
       drops below 1.5 (highly predictable pattern).
    3. **Escalation**: Tracks cumulative warnings and escalates intervention level.
    4. **Unknown tool tracking**: Tracks tool names that failed because they
       don't exist. When *replace_unknown_tools* >= 2, replaces repeated
       unknown tool calls with a `think` call for self-correction.
    """

    def __init__(
        self,
        window_size: int = 30,
        repeat_threshold: int = 3,
        # replace_unknown_tools: N means "after N repeated calls to a non-existent tool,
        # replace with think() for self-correction. Default 0 (disabled) here, but
        # tau.json overrides this to 2 at runtime. Changing this default won't affect
        # production unless tau.json is also updated.
        replace_unknown_tools: int = 0,
        warn_threshold: int = 3,
        inject_threshold: int = 7,
        force_think_threshold: int = 11,
        end_turn_threshold: int = 15,
    ):
        self.window_size = window_size
        self.repeat_threshold = repeat_threshold
        self.replace_unknown_tools = replace_unknown_tools
        self.warn_threshold = warn_threshold
        self.inject_threshold = inject_threshold
        self.force_think_threshold = force_think_threshold
        self.end_turn_threshold = end_turn_threshold

        self.tool_call_history: deque[str] = deque(maxlen=window_size)
        self.consecutive_repeats = 0
        self.last_tool_call: str | None = None

        # Entropy warnings contribute only 0.5 toward escalation thresholds
        # (predictable pattern ≠ exact loop).
        self.total_warnings = 0
        self.entropy_warnings = 0
        self.tool_warnings: dict[str, int] = {}
        self.escalation_level = 0

        # Unknown tool tracking: tool_name -> call_count (per-turn, resets with reset())
        self.failed_tool_names: dict[str, int] = {}

    def _tool_call_key(self, tool_name: str, args: dict) -> str:
        """Serialize tool name and arguments into a comparable string key."""
        try:
            args_json = json.dumps(args, sort_keys=True)
        except (TypeError, ValueError):
            args_json = json.dumps({k: str(v) for k, v in sorted(args.items())})
        return f"{tool_name}:{args_json}"

    @staticmethod
    def _select_template(level: int, templates: list[str]) -> str:
        """Select a template by escalation level, clamped to valid range."""
        idx = min(max(level - 1, 0), len(templates) - 1)
        return templates[idx]

    def _get_warning_message(self, tool_name: str) -> str:
        template = self._select_template(self.escalation_level, _WARNING_TEMPLATES)
        return template.format(
            warning_count=self.total_warnings,
            tool_name=tool_name,
            consecutive=self.consecutive_repeats,
        )

    def _get_entropy_warning(self, tool_name: str, entropy: float) -> str:
        template = self._select_template(self.escalation_level, _ENTROPY_TEMPLATES)
        return template.format(
            warning_count=self._display_warning_count(),
            tool_name=tool_name,
            entropy=entropy,
        )

    def _effective_warnings(self) -> float:
        """Escalation-sensitive warning count (entropy warnings count as 0.5x)."""
        return self.total_warnings + 0.5 * self.entropy_warnings

    def _display_warning_count(self) -> int:
        """Total human-readable warning count (repeat + entropy)."""
        return self.total_warnings + self.entropy_warnings

    def _update_escalation_level(self) -> None:
        effective = self._effective_warnings()
        if effective >= self.end_turn_threshold:
            self.escalation_level = 4
        elif effective >= self.force_think_threshold:
            self.escalation_level = 3
        elif effective >= self.inject_threshold:
            self.escalation_level = 2
        elif effective >= self.warn_threshold:
            self.escalation_level = 1
        else:
            self.escalation_level = 0

    def detect_tool_loop(self, tool_name: str, args: dict) -> str | None:
        """Check if the current tool call indicates a loop.

        Returns a warning message if a loop is detected, or None otherwise.
        """
        key = self._tool_call_key(tool_name, args)
        self.tool_call_history.append(key)

        if key == self.last_tool_call:
            self.consecutive_repeats += 1
        else:
            self.consecutive_repeats = 1
            self.last_tool_call = key

        if self.consecutive_repeats >= self.repeat_threshold:
            self.total_warnings += 1
            self.tool_warnings[tool_name] = self.tool_warnings.get(tool_name, 0) + 1
            self._update_escalation_level()
            return self._get_warning_message(tool_name)

        if len(self.tool_call_history) >= 10:
            entropy = self._calculate_entropy()
            if entropy < 1.5:
                self.entropy_warnings += 1
                self.tool_warnings[tool_name] = self.tool_warnings.get(tool_name, 0) + 1
                self._update_escalation_level()
                return self._get_entropy_warning(tool_name, entropy)

        return None

    def _calculate_entropy(self) -> float:
        """Calculate Shannon entropy of tool call patterns in the history."""
        if not self.tool_call_history:
            return 0.0
        counts = Counter(self.tool_call_history)
        total = len(self.tool_call_history)
        return -sum((c / total) * math.log2(c / total) for c in counts.values())

    def get_escalation_info(self) -> dict:
        """Return current escalation state and derived flags."""
        return {
            "total_warnings": self.total_warnings,
            "entropy_warnings": self.entropy_warnings,
            "escalation_level": self.escalation_level,
            "tool_warnings": dict(self.tool_warnings),
            "needs_injection": self.escalation_level >= 2,
            "needs_force_think": self.escalation_level >= 3,
        }

    def reset(self) -> None:
        """Clear all detection state and reset counters."""
        self.tool_call_history.clear()
        self.consecutive_repeats = 0
        self.last_tool_call = None
        self.total_warnings = 0
        self.entropy_warnings = 0
        self.tool_warnings = {}
        self.escalation_level = 0
        self.failed_tool_names.clear()

    def record_unknown_tool(self, tool_name: str) -> int:
        """Record an unknown tool call. Returns the cumulative count for this name.

        Tracks tool names that failed because they don't exist in TOOLS.
        Used to decide whether to replace repeated unknown tool calls with think.
        """
        count = self.failed_tool_names.get(tool_name, 0) + 1
        self.failed_tool_names[tool_name] = count
        return count

    def should_replace_unknown(self, tool_name: str) -> bool:
        """Check if an unknown tool call should be replaced with think.

        Returns True when the tool has been called enough times to warrant
        replacement. Threshold is self.replace_unknown_tools (0 = off).
        """
        if self.replace_unknown_tools <= 0:
            return False
        return self.failed_tool_names.get(tool_name, 0) >= self.replace_unknown_tools

    def get_stats(self) -> dict:
        """Return current detection statistics."""
        return {
            "history_size": len(self.tool_call_history),
            "consecutive_repeats": self.consecutive_repeats,
            "entropy": self._calculate_entropy() if self.tool_call_history else 0.0,
        }
