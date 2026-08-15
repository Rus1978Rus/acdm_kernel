"""ACDM-KERNEL · damping and level hold (I9).

Two lessons from the spec, enforced in code:

- Lesson 13B: the damper MUST NOT block safety. So every call takes forced=True
  — for actions the kernel itself chooses (SNAPSHOT on BEYOND_HORIZON). The
  damper guards against plugin flapping, not against the circuit's own
  protective reactions.

- Lesson D2: escalation quotas are a mistake. Escalation is NEVER blocked. The
  damper applies only to repeats of ONE action in ONE scope (anti-flap), and the
  hold applies only to DE-escalation.
"""
from __future__ import annotations

from typing import Dict, Tuple

from .types import ActionClass, ActionRequest


class Damper:
    """Minimum interval between repeats of (action, scope). Never blocks a new scope."""

    def __init__(self) -> None:
        self._last: Dict[Tuple[ActionClass, str], int] = {}

    def allow(self, request: ActionRequest, min_interval: int, cycle: int,
              forced: bool = False) -> bool:
        if forced:
            return True                      # lesson 13B: safety is not damped
        key = (request.action, request.scope)
        last = self._last.get(key)
        if last is not None and cycle - last < min_interval:
            return False
        self._last[key] = cycle
        return True


class EscalationHold:
    """De-escalation requires deescalate_hold_cycles stable cycles.

    Escalation is instant, always (lesson D2).
    """

    def __init__(self) -> None:
        self._stable_since: int | None = None
        self._candidate = None

    def check(self, current_level: int, wants_level: int, cycle: int,
              hold_cycles: int) -> int:
        if wants_level > current_level:
            self._stable_since = None
            self._candidate = None
            return wants_level               # escalation is instant
        if wants_level < current_level:
            if self._candidate != wants_level:
                self._candidate = wants_level
                self._stable_since = cycle
                return current_level
            if cycle - self._stable_since < hold_cycles:
                return current_level         # hold: stability not yet proven
            self._stable_since = None
            self._candidate = None
            return wants_level
        return current_level
