"""ACDM-KERNEL · wiring regression (audit findings A/B/D).

KEY: every check runs through the LIVE kernel.cycle, not through an isolated
call to the learner. Bypassing the live circuit is exactly what used to hide the
disconnected learning loop.

Run:
    python3 tests/test_wiring.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acdm_kernel import (                      # noqa: E402
    AuthorRole, Change, Kernel, Signal,
)
from patterns.resilience import plugin as rp   # noqa: E402
from patterns.ai_agent import plugin as ap     # noqa: E402

PASSED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if not condition:
        raise AssertionError(f"FAIL {name}: {detail}")
    PASSED += 1
    print(f"  ok  {name}")


def fresh(plugin_specs, ladder, **overrides):
    specs = {s.name: s for s in plugin_specs}
    for name, val in overrides.items():
        specs[name] = type(specs[name])(name, specs[name].tier, val)
    return Kernel(specs=specs, ladder=ladder)


def sig(name, value, conf=1.0, cycle=0):
    return Signal(name, value, conf, cycle)


# ---------------------------------------------------------------------------
# A. escalate_bias actually affects the decision (was: a dead parameter)
# ---------------------------------------------------------------------------

def test_A_bias_is_live():
    def level_at(bias):
        k = fresh(rp.STANDARD_SPECS, rp.LADDER_RESILIENCE)
        k.apply_change(Change("escalate_bias", bias, AuthorRole.HUMAN, "test"))
        # score 0.28 — just BELOW the Z2 threshold (0.30): Z1 without a shift, Z2 with one
        return k.cycle([sig("error_rate", 0.28, cycle=0)], rp.estimator, cycle=0).level
    from acdm_kernel import Level
    check("A1 without bias, score 0.28 -> Z1", level_at(0.0) is Level.Z1)
    check("A2 escalate_bias=0.05 -> the same score escalates to Z2",
          level_at(0.05) is Level.Z2, f"level={level_at(0.05)}")

    # A3: a plugin without escalate_bias does not crash and behaves as bias=0.
    k = fresh(ap.STANDARD_SPECS, ap.LADDER_AI_AGENT)
    d = k.cycle([sig("output_anomaly", 0.28, cycle=0)], ap.estimator, cycle=0)
    check("A3 a plugin without escalate_bias works (bias is optional)",
          d.level is Level.Z1, f"level={d.level}")


# ---------------------------------------------------------------------------
# B. Live kernel memory is populated; governance reaches the learner
# ---------------------------------------------------------------------------

def test_B_memory_is_live():
    # B1: a Tier-C threshold from governance now reaches the learner THROUGH the live loop.
    k = fresh(rp.STANDARD_SPECS, rp.LADDER_RESILIENCE)
    learner = rp.ResilienceLearner()
    k.apply_change(Change("learner_min_observations", 3.0, AuthorRole.HUMAN, "op"))
    for c in range(8):
        v = 0.7 if c % 2 == 0 else 0.3
        k.cycle([sig("error_rate", v, cycle=c), sig("saturation", v, cycle=c)],
                rp.estimator, learner, cycle=c)
    applied = [e for e in k.audit_events()
               if e.kind == "CHANGE" and e.payload.get("param") == "escalate_bias"]
    check("B1 governance threshold reaches the learner on the live circuit",
          len(applied) >= 1, "learner did not propose through the live loop — memory not wired")

    # B2: memory_state_hash is no longer a constant.
    k = fresh(rp.STANDARD_SPECS, rp.LADDER_RESILIENCE)
    hashes = {k.cycle([sig("error_rate", 0.1 * c, cycle=c)], rp.estimator, cycle=c)
              .memory_state_hash for c in range(5)}
    check("B2 memory_state_hash varies between differing cycles",
          len(hashes) == 5, f"distinct={len(hashes)} (a constant = not wired)")

    # B3: live memory actually holds state.
    k = fresh(rp.STANDARD_SPECS, rp.LADDER_RESILIENCE)
    k.cycle([sig("error_rate", 0.9, cycle=0)], rp.estimator, cycle=0)
    mem = dict(k.memory_view())
    check("B3 kernel memory holds last_score/last_level",
          "last_score" in mem and "last_level" in mem, f"mem={mem}")


# ---------------------------------------------------------------------------
# D. Every event is cycle-stamped (was: CHANGE and forced SNAPSHOT = 0)
# ---------------------------------------------------------------------------

def test_D_cycle_stamped():
    k = fresh(rp.STANDARD_SPECS, rp.LADDER_RESILIENCE)
    k.cycle([sig("error_rate", 0.5, cycle=42)], rp.estimator, cycle=42)
    k.apply_change(Change("escalate_bias", 0.05, AuthorRole.LEARNER, "obs=60"))
    change_ev = [e for e in k.audit_events() if e.kind == "CHANGE"][-1]
    check("D1 CHANGE event carries the current tick, not 0",
          change_ev.cycle == 42, f"cycle={change_ev.cycle}")

    # D2: the forced SNAPSHOT on BEYOND_HORIZON is cycle-stamped too.
    k = fresh(rp.STANDARD_SPECS, rp.LADDER_RESILIENCE)
    k.cycle([sig("error_rate", 0.0, cycle=0)], rp.estimator, cycle=100)  # beyond=30
    snap = [e for e in k.audit_events()
            if e.kind == "INTENT" and e.payload.get("action") == "SNAPSHOT"][0]
    check("D2 forced SNAPSHOT carries the cycle tick, not 0",
          snap.cycle == 100, f"cycle={snap.cycle}")


if __name__ == "__main__":
    print("ACDM-KERNEL · wiring regression (findings A/B/D)")
    for t in (test_A_bias_is_live, test_B_memory_is_live, test_D_cycle_stamped):
        print(f"[{t.__name__}]")
        t()
    print(f"\nALL GREEN — {PASSED} checks passed")
