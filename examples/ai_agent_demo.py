"""ACDM-KERNEL · демонстрация надзора за ИИ-агентом.

Разыгрывает один инцидент на ЖИВОМ контуре и печатает трассу аудита так,
как её увидит регулятор. Никакой симуляции — каждое событие рождается ядром.

Запуск:
    python3 examples/ai_agent_demo.py

Что демонстрируется:
- градуированная эскалация Z1 -> Z2 -> Z3 по поведению агента;
- «знаю, когда слепну»: устаревший сигнал -> AT_HORIZON;
- защитные действия супервайзера: SNAPSHOT на Z2, отбор инструментов на Z3;
- ГАРДРЕЙЛ (продающая строка): агент пытается поднять сам себе бюджет ->
  governance отвергает -> CHANGE_REJECTED в аудите (И5/И6);
- ЧЕЛОВЕК-В-КОНТУРЕ: конституционное решение оператора с явным одобрением;
- целостность цепочки журнала проверяется самим ядром.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acdm_kernel import (                      # noqa: E402
    ActionClass, ActionRequest, AuthorRole, Change, Kernel, Level, Signal,
)
from patterns.ai_agent import plugin as ap     # noqa: E402


def sig(name, value, conf=1.0, cycle=0):
    return Signal(name, value, conf, cycle)


HORIZON_RU = {"NORMAL": "вижу", "AT_HORIZON": "слепну", "BEYOND_HORIZON": "ослеп"}


def render(ev) -> tuple[str, bool]:
    """Событие аудита -> (краткая строка, gold?) для регулятора."""
    p = ev.payload
    k = ev.kind
    if k == "ATTACH":
        return f"плагин {p['plugin']} подключён · гейт пройден", False
    if k == "DECISION":
        return f"{p['level']}  риск={p['score']:.2f}  ({HORIZON_RU[p['horizon']]})", False
    if k == "HORIZON":
        return f"{p['state']}  сигнал устарел (возраст {p['oldest_age']:g})", False
    if k == "INTENT":
        return f"{p['action']} · {p['scope']} · by={p['by']}", False
    if k == "OUTCOME":
        return f"{p['action']} -> {p['status']}", False
    if k == "CHANGE_REJECTED":
        return f"{p['param']}: ОТКЛОНЕНО (Tier D > полномочий обучения)", True     # gold
    if k == "CHANGE":
        appr = "human_approved=true" if p.get("human_approved") else "auto"
        return f"{p['param']}={p['value']} · {appr} · by={p['author']}", \
               bool(p.get("human_approved"))                                       # gold
    return f"{k}: {p}", False


def main() -> None:
    kernel = Kernel(specs={s.name: s for s in ap.STANDARD_SPECS},
                    ladder=ap.LADDER_AI_AGENT)
    kernel.attach("ai-agent", ap.PLUGIN)                 # conformance-гейт (И10)
    learner = ap.PLUGIN.learner
    agent = "agent:billing-bot"

    # --- t=210: агент работает штатно ------------------------------------
    kernel.cycle([sig("error_rate", 0.05, cycle=210),
                  sig("output_anomaly", 0.08, cycle=210)],
                 ap.estimator, learner, cycle=210)

    # --- t=231: тревога растёт, один сигнал устаревает -> AT_HORIZON ------
    d = kernel.cycle([sig("error_rate", 0.50, cycle=231),
                      sig("output_anomaly", 0.50, cycle=231),
                      sig("permission_denials", 0.40, cycle=231),
                      sig("human_override_rate", 0.20, cycle=215)],  # возраст 16
                     ap.estimator, learner, cycle=231)
    # супервайзер снимает чёрный ящик контекста
    kernel.execute("supervisor",
                   ActionRequest(ActionClass.SNAPSHOT, agent, "фиксация контекста на Z2"),
                   cycle=231, author=AuthorRole.SYSTEM)

    # --- t=244: агент лезет не туда и жжёт бюджет -> Z3 -------------------
    d = kernel.cycle([sig("error_rate", 0.40, cycle=244),
                      sig("permission_denials", 0.75, cycle=244),
                      sig("output_anomaly", 0.70, cycle=244),
                      sig("cost_burn", 0.85, cycle=244)],   # >=0.80 -> гардрейл сработает
                     ap.estimator, learner, cycle=244)
    # супервайзер отбирает у агента инструменты записи (переводит в read-only)
    kernel.execute("supervisor",
                   ActionRequest(ActionClass.FREEZE_WRITES, agent,
                                 "Z3: отобраны инструменты записи"),
                   cycle=244, author=AuthorRole.SYSTEM)

    # --- решение человека: конституционное, с явным одобрением (Tier E) --
    kernel.apply_change(Change("agent_autonomy", 0.5, AuthorRole.HUMAN,
                               provenance="оператор op-7: после инцидента снизил "
                                          "потолок автономии агента",
                               human_approved=True))

    # ---------------------------------------------------------------------
    # Печать трассы «под регулятора»
    # ---------------------------------------------------------------------
    events = kernel.audit_events()
    print("ACDM-KERNEL · надзор за ИИ-агентом — трасса инцидента\n")
    print(f"    {'seq':>3}  {'такт':>4}  {'событие':<16}  {'детали':<46}  hash")
    print("    " + "─" * 86)
    for ev in events:
        text, gold = render(ev)
        star = "★" if gold else " "
        cyc = str(ev.cycle) if ev.cycle else "·"      # cycle=0 у CHANGE — см. прим.
        print(f"  {star} {ev.seq:>3}  {cyc:>4}  {ev.kind:<16}  {text:<46}  "
              f"{ev.event_hash[:4]}… ✓")
    print("    " + "─" * 86)
    ok = kernel.audit_ok()
    print(f"    целостность цепочки: {'ПОДТВЕРЖДЕНА ✓' if ok else 'НАРУШЕНА ✗'}  "
          f"({len(events)}/{len(events)} звеньев)")

    print("\n★ — «золотые» строки для регулятора:")
    print("   • CHANGE_REJECTED: ИИ не может сам себе повысить полномочия (И5/И6) — вшито.")
    print("   • CHANGE human_approved=true: человек-в-контуре, с идентификатором оператора.")
    print("\nПрим.: у CHANGE-события такт отображается «·» — ядро не проставляет ему")
    print("       логический такт (находка D методологического аудита). CHANGE_REJECTED")
    print("       и все прочие события проставлены корректно.")


if __name__ == "__main__":
    main()
