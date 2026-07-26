"""ACDM-KERNEL · circuit types.

Domain-neutral structures. The pattern vocabulary (blast radius, pressure,
agent, overspend...) does NOT belong here — it lives in the plugins.

A logical clock instead of wall-time: determinism (I4) demands that the same
input yields the same result, audit hashes included.
"""
from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


class Level(enum.IntEnum):
    """Degradation ladder (I7). Value order = order of severity."""
    Z1 = 1   # normal observation
    Z2 = 2   # heightened observation
    Z3 = 3   # containment
    Z4 = 4   # emergency containment


class HorizonState(enum.IntEnum):
    """Model of the kernel's own observability (I8)."""
    NORMAL = 0
    AT_HORIZON = 1
    BEYOND_HORIZON = 2


class ActionClass(enum.Enum):
    OBSERVE = "OBSERVE"
    SNAPSHOT = "SNAPSHOT"
    FREEZE_WRITES = "FREEZE_WRITES"
    QUARANTINE = "QUARANTINE"
    ROLLBACK = "ROLLBACK"
    KILL_DISPOSABLE = "KILL_DISPOSABLE"
    EXPAND = "EXPAND"                    # growth: forbidden at high levels
    OPERATOR_ESCALATION = "OPERATOR_ESCALATION"


class Tier(enum.IntEnum):
    """Change-authority tiers (I5/I6). E — human only."""
    B = 1   # environment: may tune the learning loop (with provenance)
    C = 2   # statistics: learning loop (with provenance)
    D = 3   # policy: operator only
    E = 4   # constitution: human only, explicit approval required


class AuthorRole(enum.Enum):
    SYSTEM = "system"     # the kernel itself (internal changes, not via governance)
    HUMAN = "human"       # operator console
    LEARNER = "learner"   # the learning loop (Gamma pattern)


class KernelViolation(PermissionError):
    """An attempt to breach an invariant. The kernel's only answer is an exception."""


@dataclass(frozen=True)
class Signal:
    name: str
    value: float
    confidence: float
    cycle: int            # logical clock at which the signal was produced


@dataclass(frozen=True)
class Score:
    value: float
    confidence: float
    features: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionRequest:
    action: ActionClass
    scope: str
    reason: str


@dataclass(frozen=True)
class ActionResult:
    request: ActionRequest
    status: str           # APPLIED / BLOCKED_BY_LEVEL / BLOCKED_BY_LOCK / BLOCKED_BY_DAMPING
    cycle: int


@dataclass(frozen=True)
class Change:
    """A proposed parameter change. There is no other path to writing parameters."""
    param: str
    new_value: float
    author: AuthorRole
    provenance: str       # required for LEARNER: where the proposal came from
    human_approved: bool = False


@dataclass(frozen=True)
class Decision:
    level: Level
    score: Score
    horizon: HorizonState
    memory_state_hash: str
    cycle: int


def canonical_hash(obj: Any) -> str:
    """Canonical hash of state (sorted keys, stable floats)."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
