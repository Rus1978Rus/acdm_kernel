"""ACDM-KERNEL · the circuit.

A composition of seven elements (K1–K7). The invariants enforced here by code,
not promised in prose:

- I1: the executor is private. A plugin physically cannot call the actuator
  directly — PluginFacade has no such attribute.
- I2: audit order — INTENT before execution, OUTCOME after.
- I3: append-only audit with a hash chain (audit.py).
- I4: determinism — logical cycles, canonical hashes; the same input yields the
  same Decision + the same audit.
- I5/I6: parameters are written only through governance (governance.py).
- I7: the level ladder — an action outside the level's clearance is rejected
  BEFORE the executor.
- I8: BEYOND_HORIZON forces the circuit into Z4 and forces a SNAPSHOT —
  regardless of what the plugin's estimator "thinks".
- I9: damper and hold (damping.py); escalation is never blocked.
- I10: a plugin attaches only after passing the conformance gate on an ISOLATED
  probe kernel — the check never mutates the live circuit.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Callable, Dict, Iterable, Mapping, Optional

from .audit import AuditSpine
from .damping import Damper, EscalationHold
from .governance import Governance, ParamSpec
from .horizon import HorizonModel
from .types import (
    ActionClass, ActionRequest, ActionResult, AuthorRole, Change, Decision,
    HorizonState, KernelViolation, Level, Score, Signal, Tier, canonical_hash,
)


class Ladder:
    """I7: which level admits which action classes."""

    def __init__(self, escalate: Mapping[float, Level],
                 allowed: Mapping[Level, frozenset]) -> None:
        self._escalate = dict(sorted(escalate.items()))  # threshold -> level
        self._allowed = dict(allowed)

    def level_for(self, score: Score) -> Level:
        level = Level.Z1
        for threshold, lv in self._escalate.items():
            if score.value >= threshold:
                level = lv
        return level

    def permits(self, level: Level, action: ActionClass) -> bool:
        return action in self._allowed[level]


class _Executor:
    """K4, private (I1). The only path to the actuator is through Kernel.execute."""

    def __init__(self, actuator: Optional[Callable[[ActionRequest], str]] = None) -> None:
        self._locks: Dict[str, ActionClass] = {}
        self._actuator = actuator or (lambda req: "APPLIED")

    def do(self, request: ActionRequest, cycle: int) -> ActionResult:
        return ActionResult(request=request, status=self._actuator(request), cycle=cycle)

    def lock(self, scope: str, action: ActionClass) -> bool:
        if scope in self._locks:
            return False
        self._locks[scope] = action
        return True

    def locks(self) -> Mapping[str, ActionClass]:
        return MappingProxyType(self._locks)


class PluginFacade:
    """Everything a plugin CAN see and do. Its role is bound at attach (I1).

    The facade deliberately has no executor / governance / audit attributes —
    checked by the conformance gate (T1).
    """

    def __init__(self, kernel: "Kernel", name: str, role: AuthorRole) -> None:
        object.__setattr__(self, "_kernel", kernel)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_role", role)

    def __setattr__(self, key, value):  # the facade is immutable from outside
        raise KernelViolation(f"facade read-only: {key}")

    @property
    def role(self) -> AuthorRole:
        return self._role

    def request_action(self, request: ActionRequest) -> ActionResult:
        return self._kernel.execute(self._name, request)

    def read_memory(self) -> Mapping[str, float]:
        return self._kernel.memory_view()

    def propose_change(self, change: Change) -> None:
        if change.author is not self._role:
            raise KernelViolation("facade writes only under its own role (I5)")
        self._kernel.apply_change(change)


class Kernel:
    """K1–K7 in one circuit. Time is whole logical cycles (I4)."""

    def __init__(self, *, specs: Mapping[str, ParamSpec], ladder: Ladder,
                 actuator: Optional[Callable[[ActionRequest], str]] = None) -> None:
        self._specs = dict(specs)
        self._gov = Governance(self._specs)
        self._ladder = ladder
        self._audit = AuditSpine()
        self._horizon = HorizonModel(
            warn_age=self._gov.value("horizon_warn_age"),
            beyond_age=self._gov.value("horizon_beyond_age"),
        )
        self._damper = Damper()
        self._hold = EscalationHold()
        self._executor = _Executor(actuator)
        self._memory: Dict[str, float] = {}
        self._level = Level.Z1
        self._cycle = 0                          # logical clock: the last tick seen
        self._plugins: Dict[str, PluginFacade] = {}

    # ---------- observability for the outside world (read-only) ----------

    @property
    def level(self) -> Level:
        return self._level

    def memory_view(self) -> Mapping[str, float]:
        return MappingProxyType(self._memory)   # I3: memory is never mutated from outside

    def audit_events(self):
        return self._audit.events()

    def audit_ok(self) -> bool:
        return self._audit.verify()

    # ---------- main loop (K1→K2→K3, with K5/K6/K7 inside) ----------

    def cycle(self, signals: Iterable[Signal], estimator,
              learner=None, cycle: int = 0) -> Decision:
        signals = list(signals)
        self._cycle = cycle                     # only cycle() advances the logical clock

        # I8: horizon first — trust in the estimate depends on it.
        hstate = self._horizon.update(signals, cycle)
        if hstate is not HorizonState.NORMAL:
            self._audit.append(cycle, "HORIZON",
                               {"state": hstate.name, "oldest_age": self._horizon.oldest_age})

        score = estimator(signals)
        score = Score(value=score.value,
                      confidence=score.confidence * self._horizon.confidence_factor(),
                      features=score.features)

        # Optional cross-cutting escalation-sensitivity knob (closes audit finding
        # A): escalate_bias shifts the score used to pick the level, if the param
        # is registered in governance; otherwise 0 and behavior is unchanged. The
        # RAW risk is what goes to the audit and Decision — the shift only affects
        # the level.
        bias = self._gov.get("escalate_bias", 0.0)
        ladder_score = Score(value=min(1.0, max(0.0, score.value + bias)),
                             confidence=score.confidence, features=score.features)
        level = self._ladder.level_for(ladder_score)
        if hstate is HorizonState.BEYOND_HORIZON:
            level = Level.Z4                    # I8: we do not trust the estimate

        self._level = Level(self._hold.check(
            int(self._level), int(level), cycle,
            hold_cycles=int(self._gov.value("deescalate_hold_cycles"))))

        if hstate is HorizonState.BEYOND_HORIZON:
            # Black-box snapshot BEFORE observability is lost; forced — lesson 13B.
            self.execute("__kernel__", ActionRequest(
                ActionClass.SNAPSHOT, scope="__kernel__",
                reason="BEYOND_HORIZON: blind emergency state"),
                forced=True, author=AuthorRole.SYSTEM)

        # K6: the learning loop sees LIVE governance values and kernel memory
        # (closes finding B: memory used to be always empty, so Tier-C thresholds
        # never reached the learner).
        if learner is not None:
            learner_memory = {**self._gov.snapshot(), **self._memory}
            for change in learner(signals, learner_memory):
                try:
                    self.apply_change(change)
                except KernelViolation as exc:
                    # a learning-loop breach is an audit event, not a crash of the circuit
                    self._audit.append(cycle, "CHANGE_REJECTED",
                                       {"param": change.param, "reason": str(exc)})

        # Kernel memory evolves -> memory_state_hash stops being a constant and
        # starts certifying state (closes finding B).
        self._memory["last_score"] = score.value
        self._memory["last_level"] = float(self._level)

        decision = Decision(level=self._level, score=score, horizon=hstate,
                            memory_state_hash=canonical_hash(self._memory),
                            cycle=cycle)
        self._audit.append(cycle, "DECISION", {
            "level": self._level.name, "score": score.value,
            "confidence": score.confidence, "horizon": hstate.name,
            "memory_hash": decision.memory_state_hash})
        return decision

    # ---------- actions (K4 via I2/I7/I9) ----------

    def execute(self, plugin_name: str, request: ActionRequest, *,
                cycle: Optional[int] = None, forced: bool = False,
                author: AuthorRole = AuthorRole.LEARNER) -> ActionResult:
        at = self._cycle if cycle is None else cycle   # default tick is the current one (finding D)
        if not self._ladder.permits(self._level, request.action):
            result = ActionResult(request, "BLOCKED_BY_LEVEL", at)
            self._audit.append(at, "OUTCOME", {
                "by": plugin_name, "action": request.action.name,
                "scope": request.scope, "status": result.status})
            return result

        damping = int(self._gov.value("damping_min_interval"))
        if not self._damper.allow(request, damping, at, forced=forced):
            result = ActionResult(request, "BLOCKED_BY_DAMPING", at)
            self._audit.append(at, "OUTCOME", {
                "by": plugin_name, "action": request.action.name,
                "scope": request.scope, "status": result.status})
            return result

        # I2: INTENT before execution.
        self._audit.append(at, "INTENT", {
            "by": plugin_name, "action": request.action.name,
            "scope": request.scope, "reason": request.reason,
            "author": author.value, "params_hash": self._gov.params_hash()})

        result = self._executor.do(request, at)

        self._audit.append(at, "OUTCOME", {
            "by": plugin_name, "action": request.action.name,
            "scope": request.scope, "status": result.status})
        return result

    # ---------- parameters (K6 via governance) ----------

    def apply_change(self, change: Change, *, cycle: Optional[int] = None) -> None:
        self._gov.apply(change)               # raises KernelViolation on a breach
        at = self._cycle if cycle is None else cycle   # finding D: CHANGE events carry a tick
        self._audit.append(at, "CHANGE",
                           {"param": change.param, "value": change.new_value,
                            "author": change.author.value,
                            "provenance": change.provenance,
                            "human_approved": change.human_approved})

    # ---------- attaching plugins (I10) ----------

    def _spawn_probe(self) -> "Kernel":
        """An isolated copy of the circuit for the conformance gate.

        A plugin's checks MUST NOT mutate the live kernel: not its level, not its
        audit, not its parameters. The probe gets a fresh governance with the same
        specs.
        """
        return Kernel(specs=self._specs, ladder=self._ladder)  # fresh defaults

    def attach(self, name: str, plugin, role: AuthorRole = AuthorRole.LEARNER) -> PluginFacade:
        from .conformance import run_conformance    # local import: avoids a cycle

        report = run_conformance(self._spawn_probe, plugin, role)
        if not report.passed:
            raise KernelViolation(f"conformance failed (I10): {report.failures}")
        # Assigning a role is constitutional: role:{name} is registered as Tier E (I5).
        role_spec = Governance.role_spec(name)
        self._specs[role_spec.name] = role_spec
        self._gov.add_spec(role_spec)
        facade = PluginFacade(self, name, role)
        self._plugins[name] = facade
        self._audit.append(0, "ATTACH", {"plugin": name, "role": role.value})
        return facade

    def _make_test_facade(self, plugin_name: str, role: AuthorRole) -> PluginFacade:
        """A facade for the conformance surface checks (T1). Not registered."""
        return PluginFacade(self, plugin_name, role)
