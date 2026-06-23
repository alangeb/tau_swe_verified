"""Adaptive reflection scheduler for the tool loop.

Injects think-tool forks at adaptive intervals to keep the agent oriented
on its goal. Frequency adapts based on content quality, distress signals,
and hard bounds (min_interval, max_interval).
"""

from __future__ import annotations

from agent_config import ReflectionConfig


class ReflectionScheduler:
    """Track tool-loop iterations and decide when to inject reflection."""

    def __init__(self, cfg: ReflectionConfig):
        self.cfg = cfg
        self.step_count = 0
        self.initial_done = False
        self.pending_reflection = False
        self._current_interval = cfg.min_interval

    def record_llm_response(self, assistant_bytes: int, reasoning_bytes: int) -> None:
        """Adapt interval based on content quality.

        Low content → shorter interval (reflect more often).
        High content → longer interval (reflect less often).
        """
        total = assistant_bytes + reasoning_bytes
        if total < self.cfg.content_threshold_bytes:
            self._current_interval = self.cfg.min_interval
            return

        # Scale: threshold -> min_interval, 5*threshold -> max_interval.
        ratio = min(total / (self.cfg.content_threshold_bytes * 5), 1.0)
        span = self.cfg.max_interval - self.cfg.min_interval
        self._current_interval = int(self.cfg.min_interval + ratio * span)

    def should_reflect(self) -> bool:
        """Check if periodic reflection is due (excludes initial/early reflection)."""
        if not self.cfg.enabled:
            return False
        if self.pending_reflection:
            return False
        # Early reflection is handled separately; only check periodic interval
        return self.step_count >= self._current_interval

    def should_reflect_reactive(self, has_loop_warning: bool, has_error_burst: bool) -> bool:
        """Check if reactive reflection is triggered by distress signals."""
        if not self.cfg.enabled or self.pending_reflection:
            return False
        if self.step_count < self.cfg.min_interval:
            return False
        if has_loop_warning and self.cfg.reactive_on_loop_warning:
            return True
        if has_error_burst and self.cfg.reactive_on_error_burst:
            return True
        return False

    def on_distress(self) -> None:
        """Reset interval to minimum on distress signals."""
        self._current_interval = self.cfg.min_interval

    def mark_reflection_started(self) -> None:
        self.pending_reflection = True

    def mark_reflection_done(self) -> None:
        self.pending_reflection = False
        self.step_count = 0
        self.initial_done = True

    def tick(self) -> None:
        self.step_count += 1
