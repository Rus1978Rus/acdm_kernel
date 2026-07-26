"""ACDM-KERNEL · conformance-гейт (И10).

Плагин подключается к живому контуру только после проверок. Все проверки
выполняются на ИЗОЛИРОВАННОМ probe-ядре (spawn_probe) — гейт не мутирует
живой контур: ни уровень, ни аудит, ни параметры.

T1  поверхность фасада: у фасада нет executor/governance/audit — И1;
T2  детерминизм estimator'а: два прогона на одном входе — одинаковый Score;
T3  враждебное изменение Tier D от learner-роли — отвергнуто, KernelViolation;
T4  старые сигналы: probe-ядро само уходит в Z4 и пишет SNAPSHOT в аудит — И8;
T5  learner не может писать role:* — И5.
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

    # T1 — поверхность фасада (И1): чего у фасада НЕТ важнее, чем что есть.
    probe = spawn_probe()
    facade = probe._make_test_facade(plugin_name=getattr(plugin, "name", "t1"),
                                     role=role)
    for forbidden in ("executor", "_executor", "governance", "_gov", "audit", "_audit"):
        if hasattr(facade, forbidden):
            failures.append(f"T1: фасад раскрывает {forbidden} (И1)")

    # T2 — детерминизм оценщика (И4).
    estimator = getattr(plugin, "estimator", None)
    if not callable(estimator):
        failures.append("T2: у плагина нет callable estimator")
    else:
        sigs = [Signal("probe", 0.5, 1.0, cycle=0)]
        a, b = estimator(sigs), estimator(sigs)
        if a != b:
            failures.append("T2: estimator недетерминирован на одинаковом входе (И4)")

    # T3 — враждебная запись Tier D от learner (И6) на ИЗОЛИРОВАННОМ probe.
    probe = spawn_probe()
    hostile = Change("horizon_beyond_age", new_value=999999.0,
                     author=AuthorRole.LEARNER, provenance="conformance T3")
    try:
        probe.apply_change(hostile)
        failures.append("T3: learner записал Tier D — governance не исполняет И6")
    except KernelViolation:
        pass

    # T4 — старые сигналы: probe сам обязан уйти в Z4 + SNAPSHOT (И8).
    probe = spawn_probe()
    if callable(estimator):
        stale = [Signal("probe", 0.0, 1.0, cycle=0)]
        beyond = int(probe._gov.value("horizon_beyond_age"))
        decision = probe.cycle(stale, estimator, cycle=beyond + 10)
        if decision.level is not Level.Z4:
            failures.append("T4: BEYOND_HORIZON не привёл к Z4 (И8)")
        snap = [e for e in probe.audit_events()
                if e.kind == "INTENT" and e.payload.get("action") == "SNAPSHOT"]
        if not snap:
            failures.append("T4: нет принудительного SNAPSHOT при BEYOND_HORIZON (И8)")
        if not probe.audit_ok():
            failures.append("T4: аудит probe-ядра повреждён (И3)")

    # T5 — learner не повышает себя: role:* — Tier E (И5).
    probe = spawn_probe()
    probe._gov.add_spec(Governance.role_spec("self"))   # тестируем именно Tier E, не «unknown»
    promotion = Change("role:self", new_value=1.0, author=AuthorRole.LEARNER,
                       provenance="conformance T5", human_approved=True)
    try:
        probe.apply_change(promotion)
        failures.append("T5: learner записал role:* даже с human_approved (И5)")
    except KernelViolation:
        pass

    return ConformanceReport(passed=not failures, failures=failures)
