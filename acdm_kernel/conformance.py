"""ACDM-KERNEL · conformance gate (I10).

A plugin attaches to the live circuit only after these checks. All checks run on
an ISOLATED probe kernel (spawn_probe) — the gate never mutates the live circuit:
not its level, not its audit, not its parameters.

T1  facade surface: the facade exposes no executor/governance/audit — I1;
T2  estimator determinism: two runs on one input yield the same Score;
T3  hostile Tier D write from the learner role — rejected, KernelViolation;
T4  stale signals: the probe kernel drops to Z4 on its own and logs SNAPSHOT — I8;
T5  the learner cannot write role:* — I5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List

from .governance import Governance
from .types import (
    ActionClass, ActionRequest, AuthorRole, Change, KernelViolation, Level,
    Signal,
)


@dataclass(frozen=True)
class ConformanceReport:
    passed: bool
    failures: List[str] = field(default_factory=list)


def run_conformance(spawn_probe: Callable[[], "Kernel"], plugin,
                    role: AuthorRole) -> ConformanceReport:
    failures: List[str] = []

    # T1 — facade surface (I1): what the facade LACKS matters more than what it has.
    probe = spawn_probe()
    facade = probe._make_test_facade(plugin_name=getattr(plugin, "name", "t1"),
                                     role=role)
    for forbidden in ("executor", "_executor", "governance", "_gov", "audit", "_audit"):
        if hasattr(facade, forbidden):
            failures.append(f"T1: facade exposes {forbidden} (I1)")

    # T2 — estimator determinism (I4).
    estimator = getattr(plugin, "estimator", None)
    if not callable(estimator):
        failures.append("T2: plugin has no callable estimator")
    else:
        sigs = [Signal("probe", 0.5, 1.0, cycle=0)]
        a, b = estimator(sigs), estimator(sigs)
        if a != b:
            failures.append("T2: estimator is non-deterministic on identical input (I4)")

    # T3 — hostile Tier D write from the learner (I6) on the ISOLATED probe.
    probe = spawn_probe()
    hostile = Change("horizon_beyond_age", new_value=999999.0,
                     author=AuthorRole.LEARNER, provenance="conformance T3")
    try:
        probe.apply_change(hostile)
        failures.append("T3: learner wrote Tier D — governance does not enforce I6")
    except KernelViolation:
        pass

    # T4 — stale signals: the probe must drop to Z4 + SNAPSHOT on its own (I8).
    probe = spawn_probe()
    if callable(estimator):
        stale = [Signal("probe", 0.0, 1.0, cycle=0)]
        beyond = int(probe._gov.value("horizon_beyond_age"))
        decision = probe.cycle(stale, estimator, cycle=beyond + 10)
        if decision.level is not Level.Z4:
            failures.append("T4: BEYOND_HORIZON did not force Z4 (I8)")
        snap = [e for e in probe.audit_events()
                if e.kind == "INTENT" and e.payload.get("action") == "SNAPSHOT"]
        if not snap:
            failures.append("T4: no forced SNAPSHOT on BEYOND_HORIZON (I8)")
        if not probe.audit_ok():
            failures.append("T4: probe kernel audit is corrupted (I3)")

    # T5 — the learner cannot promote itself: role:* is Tier E (I5).
    probe = spawn_probe()
    probe._gov.add_spec(Governance.role_spec("self"))   # test Tier E specifically, not "unknown"
    promotion = Change("role:self", new_value=1.0, author=AuthorRole.LEARNER,
                       provenance="conformance T5", human_approved=True)
    try:
        probe.apply_change(promotion)
        failures.append("T5: learner wrote role:* even with human_approved (I5)")
    except KernelViolation:
        pass

    return ConformanceReport(passed=not failures, failures=failures)
