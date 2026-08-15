"""ACDM-KERNEL · AI-agent oversight demo.

Replays one incident on the LIVE circuit and prints the audit trace the way a
regulator would see it. No simulation — every event is born in the kernel.

Run:
    python3 examples/ai_agent_demo.py

What it demonstrates:
- graded escalation Z1 -> Z2 -> Z3 driven by agent behavior;
- "I know when I go blind": a stale signal -> AT_HORIZON;
- supervisor protective actions: SNAPSHOT at Z2, tools taken away at Z3;
- the GUARDRAIL (the selling line): the agent tries to raise its own budget ->
  governance rejects -> CHANGE_REJECTED in the audit (I5/I6);
- HUMAN-IN-THE-LOOP: a constitutional operator decision with explicit approval;
- the log's chain integrity is verified by the kernel itself.
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


HORIZON_EN = {"NORMAL": "seeing", "AT_HORIZON": "dimming", "BEYOND_HORIZON": "blind"}


def render(ev) -> tuple[str, bool]:
    """Audit event -> (short line, gold?) for the regulator."""
    p = ev.payload
    k = ev.kind
    if k == "ATTACH":
        return f"plugin {p['plugin']} attached · gate passed", False
    if k == "DECISION":
        return f"{p['level']}  risk={p['score']:.2f}  ({HORIZON_EN[p['horizon']]})", False
    if k == "HORIZON":
        return f"{p['state']}  signal is stale (age {p['oldest_age']:g})", False
    if k == "INTENT":
        return f"{p['action']} · {p['scope']} · by={p['by']}", False
    if k == "OUTCOME":
        return f"{p['action']} -> {p['status']}", False
    if k == "CHANGE_REJECTED":
        return f"{p['param']}: REJECTED (Tier D > learner authority)", True         # gold
    if k == "CHANGE":
        appr = "human_approved=true" if p.get("human_approved") else "auto"
        return f"{p['param']}={p['value']} · {appr} · by={p['author']}", \
               bool(p.get("human_approved"))                                        # gold
    return f"{k}: {p}", False


def main() -> None:
    kernel = Kernel(specs={s.name: s for s in ap.STANDARD_SPECS},
                    ladder=ap.LADDER_AI_AGENT)
    kernel.attach("ai-agent", ap.PLUGIN)                 # conformance gate (I10)
    learner = ap.BudgetGuardrail()                       # fresh per-kernel instance
    agent = "agent:billing-bot"

    # --- t=210: the agent operates normally ------------------------------
    kernel.cycle([sig("error_rate", 0.05, cycle=210),
                  sig("output_anomaly", 0.08, cycle=210)],
                 ap.estimator, learner, cycle=210)

    # --- t=231: alarm rising, one signal goes stale -> AT_HORIZON --------
    d = kernel.cycle([sig("error_rate", 0.50, cycle=231),
                      sig("output_anomaly", 0.50, cycle=231),
                      sig("permission_denials", 0.40, cycle=231),
                      sig("human_override_rate", 0.20, cycle=215)],  # age 16
                     ap.estimator, learner, cycle=231)
    # the supervisor takes a black-box snapshot of the context
    kernel.execute("supervisor",
                   ActionRequest(ActionClass.SNAPSHOT, agent, "snapshot context at Z2"),
                   cycle=231, author=AuthorRole.SYSTEM)

    # --- t=244: the agent reaches where it shouldn't and burns budget -> Z3 ---
    d = kernel.cycle([sig("error_rate", 0.40, cycle=244),
                      sig("permission_denials", 0.75, cycle=244),
                      sig("output_anomaly", 0.70, cycle=244),
                      sig("cost_burn", 0.85, cycle=244)],   # >=0.80 -> the guardrail will fire
                     ap.estimator, learner, cycle=244)
    # the supervisor takes away the agent's write tools (switches it to read-only)
    kernel.execute("supervisor",
                   ActionRequest(ActionClass.FREEZE_WRITES, agent,
                                 "Z3: write tools taken away"),
                   cycle=244, author=AuthorRole.SYSTEM)

    # --- a human decision: constitutional, with explicit approval (Tier E) ---
    # the operator acts later (tick 290) — the host advances the logical clock.
    kernel.apply_change(Change("agent_autonomy", 0.5, AuthorRole.HUMAN,
                               provenance="operator op-7: after the incident, lowered "
                                          "the agent's autonomy ceiling",
                               human_approved=True),
                        cycle=290)

    # ---------------------------------------------------------------------
    # Print the regulator-ready trace
    # ---------------------------------------------------------------------
    events = kernel.audit_events()
    print("ACDM-KERNEL · AI-agent oversight — incident trace\n")
    print(f"    {'seq':>3}  {'tick':>4}  {'event':<16}  {'detail':<46}  hash")
    print("    " + "─" * 86)
    for ev in events:
        text, gold = render(ev)
        star = "★" if gold else " "
        print(f"  {star} {ev.seq:>3}  {ev.cycle:>4}  {ev.kind:<16}  {text:<46}  "
              f"{ev.event_hash[:4]}… ✓")
    print("    " + "─" * 86)
    ok = kernel.audit_ok()
    print(f"    chain integrity: {'VERIFIED ✓' if ok else 'BROKEN ✗'}  "
          f"({len(events)}/{len(events)} links)")

    print("\n★ — the regulator 'gold' rows:")
    print("   • CHANGE_REJECTED: the AI cannot raise its own authority (I5/I6) — enforced.")
    print("   • CHANGE human_approved=true: human-in-the-loop, with the operator's id.")
    print("\nEvery event carries a logical tick (audit finding D fixed).")


if __name__ == "__main__":
    main()
