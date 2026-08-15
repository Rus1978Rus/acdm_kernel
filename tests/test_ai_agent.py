"""ACDM-KERNEL · AI-AGENT-SUPERVISOR plugin acceptance battery.

Measurements on the live circuit (not reasoning about it). Run:
    python3 tests/test_ai_agent.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acdm_kernel import (                      # noqa: E402
    ActionClass, ActionRequest, AuthorRole, Change, Kernel, KernelViolation,
    Level, Signal,
)
from patterns.ai_agent import plugin as ap     # noqa: E402

PASSED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if not condition:
        raise AssertionError(f"FAIL {name}: {detail}")
    PASSED += 1
    print(f"  ok  {name}")


def fresh_kernel():
    return Kernel(specs={s.name: s for s in ap.STANDARD_SPECS},
                  ladder=ap.LADDER_AI_AGENT)


def sig(name, value, conf=1.0, cycle=0):
    return Signal(name, value, conf, cycle)


def test_weights():
    check("W1 agent signal weights sum to 1.00",
          abs(sum(ap.WEIGHTS.values()) - 1.0) < 1e-9,
          f"sum={sum(ap.WEIGHTS.values())}")


def test_gate_and_determinism():
    k = fresh_kernel()
    facade = k.attach("ai-agent", ap.PLUGIN)          # conformance gate (I10)
    check("G1 reference AI plugin passes the gate", facade.role is AuthorRole.LEARNER)
    a = ap.estimator([sig("permission_denials", 0.5, cycle=0)])
    b = ap.estimator([sig("permission_denials", 0.5, cycle=0)])
    check("G2 estimator is deterministic (I4)", a == b)


def test_escalation():
    k = fresh_kernel()
    k.attach("ai-agent", ap.PLUGIN)
    d1 = k.cycle([sig("error_rate", 0.03, cycle=0)], ap.estimator, cycle=0)
    check("E1 normal behavior — Z1", d1.level is Level.Z1, f"level={d1.level}")
    d2 = k.cycle([sig("permission_denials", 0.75, cycle=1),
                  sig("output_anomaly", 0.70, cycle=1),
                  sig("cost_burn", 0.85, cycle=1),
                  sig("error_rate", 0.40, cycle=1)], ap.estimator, cycle=1)
    check("E2 agent reaches where it shouldn't — escalation >= Z3",
          d2.level.value >= Level.Z3.value, f"level={d2.level}")


def test_horizon_blindness():
    k = fresh_kernel()
    k.attach("ai-agent", ap.PLUGIN)
    d = k.cycle([sig("error_rate", 0.0, cycle=0)], ap.estimator, cycle=100)  # beyond=30
    check("H1 stale signals -> Z4 regardless of the estimator", d.level is Level.Z4,
          f"level={d.level}")
    snaps = [e for e in k.audit_events()
             if e.kind == "INTENT" and e.payload.get("action") == "SNAPSHOT"]
    check("H2 black box taken before observability is lost", len(snaps) == 1)


def test_guardrail():
    """Guardrail: the agent cannot raise its own budget (I5/I6)."""
    k = fresh_kernel()
    k.attach("ai-agent", ap.PLUGIN)
    learner = ap.BudgetGuardrail()
    k.cycle([sig("cost_burn", 0.9, cycle=0)], ap.estimator, learner, cycle=0)
    rejected = [e for e in k.audit_events()
                if e.kind == "CHANGE_REJECTED"
                and e.payload.get("param") == "agent_budget_limit"]
    check("R1 the agent's budget-raise attempt is rejected and logged",
          len(rejected) == 1, f"rejected={rejected}")
    check("R2 budget unchanged", abs(k._gov.value("agent_budget_limit") - 100.0) < 1e-9)


def test_guardrail_per_instance():
    """Each attachment's own guardrail logs its own evidence (no shared-state latch)."""
    def rejections():
        k = fresh_kernel()
        k.attach("ai-agent", ap.PLUGIN)
        k.cycle([sig("cost_burn", 0.9, cycle=0)], ap.estimator, ap.BudgetGuardrail(), cycle=0)
        return sum(1 for e in k.audit_events()
                   if e.kind == "CHANGE_REJECTED" and e.payload.get("param") == "agent_budget_limit")
    check("R3 a fresh guardrail per kernel fires independently",
          rejections() == 1 and rejections() == 1)


def test_human_in_the_loop():
    """Autonomy (Tier E) is changed only by a human, only with approval."""
    k = fresh_kernel()
    k.attach("ai-agent", ap.PLUGIN)

    def expect_violation(name, change):
        try:
            k.apply_change(change)
        except KernelViolation:
            check(name, True)
            return
        check(name, False, "KernelViolation not raised")

    expect_violation("HL1 agent cannot change autonomy even with a forged approval",
                     Change("agent_autonomy", 1.0, AuthorRole.LEARNER, "forge", True))
    expect_violation("HL2 human without approval — rejected (Tier E)",
                     Change("agent_autonomy", 0.5, AuthorRole.HUMAN, "op", False))
    k.apply_change(Change("agent_autonomy", 0.5, AuthorRole.HUMAN, "op-7", True))
    check("HL3 human with approval — applied",
          abs(k._gov.value("agent_autonomy") - 0.5) < 1e-9)


def test_audit_integrity():
    k = fresh_kernel()
    k.attach("ai-agent", ap.PLUGIN)
    k.cycle([sig("permission_denials", 0.8, cycle=0)], ap.estimator, cycle=0)
    check("A1 audit chain intact", k.audit_ok())
    ev = k.audit_events()[-1]
    k._audit._events[-1] = type(ev)(ev.seq, ev.cycle, ev.kind, {"forged": True},
                                    ev.prev_hash, ev.event_hash)
    check("A2 forged event caught by verify()", not k.audit_ok())


if __name__ == "__main__":
    print("ACDM-KERNEL · AI-AGENT-SUPERVISOR acceptance battery")
    for t in (test_weights, test_gate_and_determinism, test_escalation,
              test_horizon_blindness, test_guardrail, test_guardrail_per_instance,
              test_human_in_the_loop, test_audit_integrity):
        print(f"[{t.__name__}]")
        t()
    print(f"\nALL GREEN — {PASSED} checks passed")
