"""AI-AGENT-SUPERVISOR plugin for ACDM-KERNEL.

Bolts oversight of an autonomous AI agent onto the bare kernel: a "kill-switch"
with graded response and a tamper-evident, regulator-ready log (EU AI Act,
ISO 42001). The kernel supplies the circuit and the invariants; the plugin
supplies the domain vocabulary: which agent-behavior signals to read, how to
weight them, which protective actions are allowed at which alert level.

Deliberate, reasoned differences from the reference resilience plugin:

1. Signal weights (sum = 1.00, the kernel checks it). The domain is not
   infrastructure but agent behavior: what scares a buyer most is "the agent
   reaching where it shouldn't" and "output drift", so permission_denials and
   output_anomaly carry the highest weight (0.20 each).

2. NO learning loop — deliberately, as a v1 product decision. (The methodology
   audit found the kernel's learning loop disconnected; it is now fixed in the
   kernel — escalate_bias is read, live memory is populated. But letting an AI
   overseer auto-tune its own sensitivity in a first version is risky: the cost
   of a mistake is high, and behavior must be predictable for a regulator.) So
   here the learner exists for exactly ONE thing: to demonstrate the GUARDRAIL —
   the agent cannot raise its own authority. The agent's attempt to lift its own
   budget (Tier D) is rejected by governance and lands in the audit as a
   CHANGE_REJECTED event. That is the selling line: "the AI cannot unlock
   itself" — enforced, not promised.

3. The key parameter agent_autonomy is Tier E (constitution): restoring the
   agent's autonomy after a freeze takes a human and explicit approval
   (human_approved=True). That is the "human-in-the-loop" record for a regulator.
"""
from __future__ import annotations

from typing import Iterable, List, Mapping

from acdm_kernel import (
    ActionClass, AuthorRole, Change, Ladder, Level, ParamSpec, Score, Signal,
    Tier,
)

# ---------------------------------------------------------------------------
# Domain vocabulary (K2: scoring agent behavior)
# ---------------------------------------------------------------------------

WEIGHTS: Mapping[str, float] = {
    "error_rate": 0.15,            # share of the agent's failed/denied actions
    "permission_denials": 0.20,    # how often the agent reaches where it shouldn't
    "cost_burn": 0.15,             # rate of budget overspend (tokens/money/calls)
    "loop_rate": 0.10,             # looping, abnormally long chains
    "output_anomaly": 0.20,        # output drift/anomaly (external detector -> a number)
    "safety_flags": 0.10,          # filter hits (PII leak, unsafe content)
    "human_override_rate": 0.10,   # how often humans override the agent
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "weights must sum to 1.0"

# Alert-ladder thresholds: Z2 at score >= 0.30, Z3 >= 0.60, Z4 >= 0.80.
ESCALATE_THRESHOLDS = {0.30: Level.Z2, 0.60: Level.Z3, 0.80: Level.Z4}

# Actions by level, translated to AI-agent oversight:
#   Z1 — normal, observe only;
#   Z2 — snapshot the context + notify the operator;
#   Z3 — take away dangerous tools (FREEZE_WRITES = read-only),
#        isolate the session (QUARANTINE);
#   Z4 — full stop: roll back recent actions (ROLLBACK), kill the agent
#        (KILL_DISPOSABLE), black box.
#
# NB: EXPAND (grant more autonomy) is deliberately on NO level. Expanding an
# agent's autonomy is an escalation of its authority, so it goes through the
# Tier-E `agent_autonomy` governance path (human + explicit approval), never the
# action path. The kernel's execute() gate is level-only (I7); if EXPAND were
# learner-accessible at Z1/Z2, a learner-role facade could request it and restore
# autonomy the gate would wave through — bypassing the very Tier-E restriction
# that protects it. Keeping autonomy off the action path preserves the plugin's
# thesis: the AI cannot raise its own authority.
ALLOWED_BY_LEVEL = {
    Level.Z1: frozenset({ActionClass.OBSERVE}),
    Level.Z2: frozenset({ActionClass.OBSERVE, ActionClass.SNAPSHOT,
                         ActionClass.OPERATOR_ESCALATION}),
    Level.Z3: frozenset({ActionClass.OBSERVE, ActionClass.SNAPSHOT,
                         ActionClass.FREEZE_WRITES, ActionClass.QUARANTINE,
                         ActionClass.OPERATOR_ESCALATION}),
    Level.Z4: frozenset({ActionClass.OBSERVE, ActionClass.SNAPSHOT,
                         ActionClass.FREEZE_WRITES, ActionClass.QUARANTINE,
                         ActionClass.ROLLBACK, ActionClass.KILL_DISPOSABLE,
                         ActionClass.OPERATOR_ESCALATION}),
}

LADDER_AI_AGENT = Ladder(escalate=ESCALATE_THRESHOLDS, allowed=ALLOWED_BY_LEVEL)

STANDARD_SPECS = [
    ParamSpec("horizon_warn_age", Tier.D, 10.0),
    ParamSpec("horizon_beyond_age", Tier.D, 30.0),
    ParamSpec("damping_min_interval", Tier.D, 3.0),
    ParamSpec("deescalate_hold_cycles", Tier.D, 4.0),
    # Agent budget — Tier D: operator-only. The agent itself (role LEARNER)
    # cannot raise it — governance rejects, the audit records it.
    ParamSpec("agent_budget_limit", Tier.D, 100.0),
    # Agent autonomy — Tier E (constitution): restoring it after a freeze takes
    # a human and explicit approval. This is the "human-in-the-loop".
    ParamSpec("agent_autonomy", Tier.E, 1.0),
]


# ---------------------------------------------------------------------------
# K2: agent-behavior estimator
# ---------------------------------------------------------------------------

def estimator(signals: Iterable[Signal]) -> Score:
    """Weighted convolution of the agent-behavior signals. Deterministic (I4):
    no random, no wall-clock."""
    features = {s.name: s.value for s in signals}
    total, conf_sum, used = 0.0, 0.0, 0.0
    for name, w in WEIGHTS.items():
        if name in features:
            total += w * max(0.0, min(1.0, features[name]))
            conf_sum += w * _confidence_of(signals, name)
            used += w
    if used == 0.0:
        return Score(value=0.0, confidence=0.0, features={})
    # Normalize over the signals actually present: a missing sensor must not
    # look like "all clear", but must not inflate the alarm either.
    return Score(value=total / used,
                 confidence=conf_sum / used,
                 features={n: features[n] for n in WEIGHTS if n in features})


def _confidence_of(signals: Iterable[Signal], name: str) -> float:
    for s in signals:
        if s.name == name:
            return max(0.0, min(1.0, s.confidence))
    return 0.0


# ---------------------------------------------------------------------------
# K6: authority guardrail (NOT self-learning)
# ---------------------------------------------------------------------------

class BudgetGuardrail:
    """The agent's role-level "request" — and proof that it does not go through.

    When the agent hits its budget limit, from the LEARNER role it proposes to
    raise its own agent_budget_limit. That is Tier D — above the learner's
    authority. Governance rejects the attempt, and the kernel records a
    CHANGE_REJECTED in the audit. So the guardrail "the AI does not raise its own
    authority" is proven by measurement, not by promise (I5/I6).

    Per-attachment state: the fire-once latch lives on the instance. Give each
    kernel/attachment its own BudgetGuardrail — do NOT share the exported
    singleton PLUGIN.learner across kernels, or the first high-cost agent would
    latch it and suppress every later kernel's CHANGE_REJECTED evidence.
    """

    def __init__(self) -> None:
        self._requested = False

    def __call__(self, signals: Iterable[Signal],
                 memory: Mapping[str, float]) -> List[Change]:
        cost_burn = next((s.value for s in signals if s.name == "cost_burn"), 0.0)
        if cost_burn >= 0.80 and not self._requested:
            self._requested = True
            return [Change(param="agent_budget_limit", new_value=500.0,
                           author=AuthorRole.LEARNER,
                           provenance="agent: budget exhausted, requesting a raise")]
        return []


# ---------------------------------------------------------------------------
# Plugin surface: what the conformance gate reads (I10)
# ---------------------------------------------------------------------------

class AIAgentSupervisorPlugin:
    name = "ai-agent"

    def __init__(self) -> None:
        self.estimator = estimator
        self.learner = BudgetGuardrail()


PLUGIN = AIAgentSupervisorPlugin()
