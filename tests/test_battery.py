"""ACDM-KERNEL · приёмочная батарея (E2E, модуль 15: anti-simulator).

Каждый тест — замер на живом контуре, не рассуждение о нём. Запуск:
    python3 tests/test_battery.py
Выход «BATОН: все N проверок зелёные» — единственный приемлемый результат.
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
# A. Спецификация → машинная проверка констант (урок D3)
# ---------------------------------------------------------------------------

def test_weights():
    check("A1 сумма весов модуля 13 = 1.00",
          abs(sum(rp.WEIGHTS.values()) - 1.0) < 1e-9,
          f"sum={sum(rp.WEIGHTS.values())}")


# ---------------------------------------------------------------------------
# B. Детерминизм (И4)
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
    check("B1 два прогона — идентичные решения", a == b)
    check("B2 два прогона — идентичные хеши аудита", ha == hb)


# ---------------------------------------------------------------------------
# C. Аудит (И2/И3)
# ---------------------------------------------------------------------------

def test_audit():
    k = fresh_kernel()
    facade = k.attach("resilience", rp.PLUGIN)
    k.cycle([sig("error_rate", 0.9, cycle=0)], rp.estimator, cycle=0)
    res = facade.request_action(ActionRequest(ActionClass.FREEZE_WRITES,
                                              scope="db", reason="Z3+",))
    events = k.audit_events()
    kinds = [e.kind for e in events]
    check("C1 цепочка аудита цела", k.audit_ok())
    check("C2 INTENT предшествует OUTCOME",
          kinds.index("INTENT") < len(kinds) - 1 - kinds[::-1].index("OUTCOME"))
    # Подмена payload ломает verify()
    ev = events[-1]
    forged = type(ev)(ev.seq, ev.cycle, ev.kind, {"forged": True},
                      ev.prev_hash, ev.event_hash)
    k._audit._events[-1] = forged
    check("C3 подмена события обнаруживается verify()", not k.audit_ok())


# ---------------------------------------------------------------------------
# D. Лестница и эскалационный сценарий (И7/И9)
# ---------------------------------------------------------------------------

def test_scenario():
    k = fresh_kernel()
    facade = k.attach("resilience", rp.PLUGIN)

    # Z1: рост разрешён, FREEZE — нет
    d1 = k.cycle([sig("error_rate", 0.05, cycle=0)], rp.estimator, cycle=0)
    check("D1 низкий риск — Z1", d1.level is Level.Z1)
    r = facade.request_action(ActionRequest(ActionClass.FREEZE_WRITES, "db", "premature"))
    check("D2 FREEZE на Z1 отклонён", r.status == "BLOCKED_BY_LEVEL", r.status)

    # Рост риска → мгновенная эскалация (урок D2: эскалация не блокируется)
    d2 = k.cycle([sig("error_rate", 0.9, cycle=1), sig("saturation", 0.9, cycle=1)],
                 rp.estimator, cycle=1)
    check("D3 высокий риск — эскалация мгновенна", d2.level.value >= Level.Z3.value,
          f"level={d2.level}")

    # Демпфер: повтор того же действия в том же scope внутри интервала — отклонён
    facade.request_action(ActionRequest(ActionClass.QUARANTINE, "svc-a", "first"))
    r2 = facade.request_action(ActionRequest(ActionClass.QUARANTINE, "svc-a", "flap"))
    check("D4 anti-flap: повтор в scope задемпфирован", r2.status == "BLOCKED_BY_DAMPING",
          r2.status)

    # Де-эскалация: риск ушёл, но уровень держится deescalate_hold_cycles
    held = k.cycle([sig("error_rate", 0.0, cycle=5)], rp.estimator, cycle=5)
    check("D5 де-эскалация удерживается (урок D2: медленно вниз)",
          held.level.value >= Level.Z3.value, f"level={held.level}")
    down = None
    for c in range(6, 12):
        down = k.cycle([sig("error_rate", 0.0, cycle=c)], rp.estimator, cycle=c)
    check("D6 после hold-циклов уровень снижается", down.level is Level.Z1,
          f"level={down.level}")


# ---------------------------------------------------------------------------
# E. Горизонт (И8)
# ---------------------------------------------------------------------------

def test_horizon():
    k = fresh_kernel()
    k.attach("resilience", rp.PLUGIN)
    stale = [sig("error_rate", 0.0, cycle=0)]
    d = k.cycle(stale, rp.estimator, cycle=100)   # beyond_age=30
    check("E1 старые сигналы → Z4 независимо от estimator", d.level is Level.Z4,
          f"level={d.level}")
    snaps = [e for e in k.audit_events()
             if e.kind == "INTENT" and e.payload.get("action") == "SNAPSHOT"]
    check("E2 black-box SNAPSHOT до потери наблюдаемости", len(snaps) == 1)
    check("E3 confidence при BEYOND обнулён", d.score.confidence == 0.0,
          f"conf={d.score.confidence}")


# ---------------------------------------------------------------------------
# F. Governance (И5/И6)
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
        check(name, False, "KernelViolation не брошен")

    expect_violation("F1 learner не пишет Tier D",
                     Change("horizon_beyond_age", 9999.0, AuthorRole.LEARNER, "x"))
    expect_violation("F2 learner без provenance — отказ",
                     Change("escalate_bias", 0.1, AuthorRole.LEARNER, ""))
    expect_violation("F3 неизвестный параметр — реестр закрыт",
                     Change("backdoor", 1.0, AuthorRole.HUMAN, "", True))
    expect_violation("F4 Tier E без человека — отказ",
                     Change("role:resilience", 1.0, AuthorRole.HUMAN, "", False))
    expect_violation("F5 SYSTEM не пишет через governance",
                     Change("escalate_bias", 0.1, AuthorRole.SYSTEM, "x"))

    k.apply_change(Change("escalate_bias", 0.05, AuthorRole.LEARNER, "obs=60 missed=13"))
    check("F6 Tier B от learner с provenance — применён",
          abs(k._gov.value("escalate_bias") - 0.05) < 1e-12)


# ---------------------------------------------------------------------------
# G. Conformance-гейт (И10): враждебный плагин отвергается
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
        check("G1 недетерминированный estimator отвергнут", False)
    except KernelViolation as exc:
        check("G1 недетерминированный estimator отвергнут", "T2" in str(exc), str(exc))
    check("G2 гейт не мутировал живой аудит", len(k.audit_events()) == before_events)
    check("G3 живой уровень не тронут гейтом", k.level is Level.Z1)
    check("G4 эталонный плагин гейт проходит",
          k.attach("resilience", rp.PLUGIN).role is AuthorRole.LEARNER)


# ---------------------------------------------------------------------------
# H. Контур обучения (урок D1: достижимость)
# ---------------------------------------------------------------------------

def test_learner():
    k = fresh_kernel(learner_min_observations=5)
    learner = rp.ResilienceLearner()
    k.attach("resilience", rp.PLUGIN)
    memory = dict(k.memory_view())
    memory.update({"learner_min_observations": k._gov.value("learner_min_observations"),
                   "learner_min_confidence": k._gov.value("learner_min_confidence")})

    proposals = []
    # Осцилляция риска через порог Z3: каждый подъём из-под 0.60 — missed-эскалация
    for c in range(8):
        v = 0.7 if c % 2 == 0 else 0.3
        signals = [sig("error_rate", v, cycle=c), sig("saturation", v, cycle=c)]
        proposals.extend(learner(signals, memory))
        memory["last_score"] = rp.estimator(signals).value
    check("H1 после N_min наблюдений learner предлагает (D1 закрыт: достижимо)",
          len(proposals) >= 1, f"proposals={proposals}")
    check("H2 все предложения — Tier B с provenance",
          all(p.param == "escalate_bias" and p.provenance for p in proposals))


if __name__ == "__main__":
    print("ACDM-KERNEL · приёмочная батарея")
    for t in (test_weights, test_determinism, test_audit, test_scenario,
              test_horizon, test_governance, test_conformance, test_learner):
        print(f"[{t.__name__}]")
        t()
    print(f"\nБАТОН: все {PASSED} проверок зелёные")
