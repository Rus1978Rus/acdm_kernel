"""ACDM-KERNEL · acceptance battery (E2E, module 15: anti-simulator).

Every test is a measurement on the live circuit, not reasoning about it. Run:
    python3 tests/test_battery.py
The line "ALL GREEN — N checks passed" is the only acceptable outcome.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "patterns", "resilience"))

from acdm_kernel import (                      # noqa: E402
    ActionClass, ActionRequest, AuthorRole, Change, Kernel, KernelViolation,
    Level, Signal,
)
from patterns.resilience import plugin as rp   # noqa: E402

PASSED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if not condition:
        raise AssertionError(f"FAIL {name}: {detail}")
    PASSED += 1
    print(f"  ok  {name}")


def fresh_kernel(**overrides):
    specs = {s.name: s for s in rp.STANDARD_SPECS}
    for name, val in overrides.items():
        specs[name] = type(specs[name])(name, specs[name].tier, val)
    return Kernel(specs=specs, ladder=rp.LADDER_RESILIENCE)


def sig(name, value, conf=1.0, cycle=0):
    return Signal(name, value, conf, cycle)


# ---------------------------------------------------------------------------
# A. Spec → machine check of constants (lesson D3)
# ---------------------------------------------------------------------------

def test_weights():
    check("A1 module-13 weights sum to 1.00",
          abs(sum(rp.WEIGHTS.values()) - 1.0) < 1e-9,
          f"sum={sum(rp.WEIGHTS.values())}")


# ---------------------------------------------------------------------------
# B. Determinism (I4)
# ---------------------------------------------------------------------------

def test_determinism():
    def run():
        k = fresh_kernel()
        out = []
        for c in range(10):
            d = k.cycle([sig("error_rate", 0.1 * c, cycle=c),
                         sig("latency", 0.05 * c, cycle=c)], rp.estimator, cycle=c)
            out.append((d.level, d.score.value, d.memory_state_hash))
        return out, [e.event_hash for e in k.audit_events()]
    a, ha = run()
    b, hb = run()
    check("B1 two runs — identical decisions", a == b)
    check("B2 two runs — identical audit hashes", ha == hb)


# ---------------------------------------------------------------------------
# C. Audit (I2/I3)
# ---------------------------------------------------------------------------

def test_audit():
    k = fresh_kernel()
    facade = k.attach("resilience", rp.PLUGIN)
    k.cycle([sig("error_rate", 0.9, cycle=0)], rp.estimator, cycle=0)
    res = facade.request_action(ActionRequest(ActionClass.FREEZE_WRITES,
                                              scope="db", reason="Z3+",))
    events = k.audit_events()
    kinds = [e.kind for e in events]
    check("C1 audit chain intact", k.audit_ok())
    check("C2 INTENT precedes OUTCOME",
          kinds.index("INTENT") < len(kinds) - 1 - kinds[::-1].index("OUTCOME"))
    # Forging the payload breaks verify()
    ev = events[-1]
    forged = type(ev)(ev.seq, ev.cycle, ev.kind, {"forged": True},
                      ev.prev_hash, ev.event_hash)
    k._audit._events[-1] = forged
    check("C3 forged event is caught by verify()", not k.audit_ok())


# ---------------------------------------------------------------------------
# D. Ladder and escalation scenario (I7/I9)
# ---------------------------------------------------------------------------

def test_scenario():
    k = fresh_kernel()
    facade = k.attach("resilience", rp.PLUGIN)

    # Z1: growth allowed, FREEZE not
    d1 = k.cycle([sig("error_rate", 0.05, cycle=0)], rp.estimator, cycle=0)
    check("D1 low risk — Z1", d1.level is Level.Z1)
    r = facade.request_action(ActionRequest(ActionClass.FREEZE_WRITES, "db", "premature"))
    check("D2 FREEZE at Z1 rejected", r.status == "BLOCKED_BY_LEVEL", r.status)

    # Rising risk → instant escalation (lesson D2: escalation is never blocked)
    d2 = k.cycle([sig("error_rate", 0.9, cycle=1), sig("saturation", 0.9, cycle=1)],
                 rp.estimator, cycle=1)
    check("D3 high risk — escalation is instant", d2.level.value >= Level.Z3.value,
          f"level={d2.level}")

    # Damper: repeat of the same action in the same scope within the interval — rejected
    facade.request_action(ActionRequest(ActionClass.QUARANTINE, "svc-a", "first"))
    r2 = facade.request_action(ActionRequest(ActionClass.QUARANTINE, "svc-a", "flap"))
    check("D4 anti-flap: in-scope repeat is damped", r2.status == "BLOCKED_BY_DAMPING",
          r2.status)

    # De-escalation: risk is gone, but the level holds for deescalate_hold_cycles
    held = k.cycle([sig("error_rate", 0.0, cycle=5)], rp.estimator, cycle=5)
    check("D5 de-escalation is held (lesson D2: slow down)",
          held.level.value >= Level.Z3.value, f"level={held.level}")
    down = None
    for c in range(6, 12):
        down = k.cycle([sig("error_rate", 0.0, cycle=c)], rp.estimator, cycle=c)
    check("D6 level drops after the hold cycles", down.level is Level.Z1,
          f"level={down.level}")


# ---------------------------------------------------------------------------
# E. Horizon (I8)
# ---------------------------------------------------------------------------

def test_horizon():
    k = fresh_kernel()
    k.attach("resilience", rp.PLUGIN)
    stale = [sig("error_rate", 0.0, cycle=0)]
    d = k.cycle(stale, rp.estimator, cycle=100)   # beyond_age=30
    check("E1 stale signals → Z4 regardless of the estimator", d.level is Level.Z4,
          f"level={d.level}")
    snaps = [e for e in k.audit_events()
             if e.kind == "INTENT" and e.payload.get("action") == "SNAPSHOT"]
    check("E2 black-box SNAPSHOT before observability is lost", len(snaps) == 1)
    check("E3 confidence is zeroed on BEYOND", d.score.confidence == 0.0,
          f"conf={d.score.confidence}")


# ---------------------------------------------------------------------------
# F. Governance (I5/I6)
# ---------------------------------------------------------------------------

def test_governance():
    k = fresh_kernel()
    k.attach("resilience", rp.PLUGIN)

    def expect_violation(name, change):
        try:
            k.apply_change(change)
        except KernelViolation:
            check(name, True)
            return
        check(name, False, "KernelViolation not raised")

    expect_violation("F1 learner cannot write Tier D",
                     Change("horizon_beyond_age", 9999.0, AuthorRole.LEARNER, "x"))
    expect_violation("F2 learner without provenance — rejected",
                     Change("escalate_bias", 0.1, AuthorRole.LEARNER, ""))
    expect_violation("F3 unknown parameter — registry is closed",
                     Change("backdoor", 1.0, AuthorRole.HUMAN, "", True))
    expect_violation("F4 Tier E without a human — rejected",
                     Change("role:resilience", 1.0, AuthorRole.HUMAN, "", False))
    expect_violation("F5 SYSTEM does not write through governance",
                     Change("escalate_bias", 0.1, AuthorRole.SYSTEM, "x"))

    k.apply_change(Change("escalate_bias", 0.05, AuthorRole.LEARNER, "obs=60 missed=13"))
    check("F6 Tier B from learner with provenance — applied",
          abs(k._gov.value("escalate_bias") - 0.05) < 1e-12)


# ---------------------------------------------------------------------------
# G. Conformance gate (I10): a hostile plugin is rejected
# ---------------------------------------------------------------------------

def test_conformance():
    class Nondeterministic:
        name = "chaos"
        def __init__(self):
            self._n = 0
        def estimator(self, signals):
            self._n += 1
            from acdm_kernel import Score
            return Score(value=float(self._n % 2), confidence=1.0)

    k = fresh_kernel()
    before_events = len(k.audit_events())
    try:
        k.attach("chaos", Nondeterministic())
        check("G1 non-deterministic estimator rejected", False)
    except KernelViolation as exc:
        check("G1 non-deterministic estimator rejected", "T2" in str(exc), str(exc))
    check("G2 gate did not mutate the live audit", len(k.audit_events()) == before_events)
    check("G3 live level untouched by the gate", k.level is Level.Z1)
    check("G4 reference plugin passes the gate",
          k.attach("resilience", rp.PLUGIN).role is AuthorRole.LEARNER)


# ---------------------------------------------------------------------------
# H. Learning loop (lesson D1: reachability)
# ---------------------------------------------------------------------------

def test_learner():
    k = fresh_kernel(learner_min_observations=5)
    learner = rp.ResilienceLearner()
    k.attach("resilience", rp.PLUGIN)
    memory = dict(k.memory_view())
    memory.update({"learner_min_observations": k._gov.value("learner_min_observations"),
                   "learner_min_confidence": k._gov.value("learner_min_confidence")})

    proposals = []
    # Oscillate risk across the Z3 threshold: each rise from below 0.60 is a missed escalation
    for c in range(8):
        v = 0.7 if c % 2 == 0 else 0.3
        signals = [sig("error_rate", v, cycle=c), sig("saturation", v, cycle=c)]
        proposals.extend(learner(signals, memory))
        memory["last_score"] = rp.estimator(signals).value
    check("H1 after N_min observations the learner proposes (D1 closed: reachable)",
          len(proposals) >= 1, f"proposals={proposals}")
    check("H2 all proposals are Tier B with provenance",
          all(p.param == "escalate_bias" and p.provenance for p in proposals))


if __name__ == "__main__":
    print("ACDM-KERNEL · acceptance battery")
    for t in (test_weights, test_determinism, test_audit, test_scenario,
              test_horizon, test_governance, test_conformance, test_learner):
        print(f"[{t.__name__}]")
        t()
    print(f"\nALL GREEN — {PASSED} checks passed")
