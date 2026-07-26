"""Reference RESILIENCE plugin for ACDM-KERNEL.

Bolts the resilience pattern (modules 01–13 of the ACDM-ST spec) onto the bare
kernel. The kernel supplies the circuit and the invariants; the plugin supplies
the domain vocabulary: which signals to read, how to weight them, which actions
at which level.

What is taken from the spec — and what is taken MODIFIED, with the reason:

1. z_score weights (module 13, verified by proofreading, sum = 1.00):
   error_rate 0.20, latency 0.15, saturation 0.20, dependency 0.10,
   freshness 0.20, entropy 0.10, trend 0.05.
   The machine check of the sum lives in the acceptance battery (lesson D3: no
   undefined symbols, every constant is observable).

2. Ladder thresholds: Z2 at score ≥ 0.30, Z3 ≥ 0.60 (the spec's Z2.5), Z4 ≥ 0.80.

3. Learning loop: WITHOUT τ-decay. Lesson D1 — τ_decay=60s against T_obs ≥ 24h
   made learning unreachable: a contradiction in the constants, caught by
   proofreading. Here, instead of time decay, an observation counter (N_min = 50)
   that does not depend on wall-clock and preserves determinism (I4).

4. Trust: a learner's proposals are considered only at confidence ≥ 0.6 (the
   spec's objectivity constant).

5. What the plugin does NOT do: it never touches the executor, never writes
   parameters directly, never assigns itself a role — all of that is sealed by
   the kernel, and the conformance gate checks it before attach (I10).
"""
from __future__ import annotations

from typing import Iterable, List, Mapping

from acdm_kernel import (
    ActionClass, AuthorRole, Change, Ladder, Level, ParamSpec, Score, Signal,
    Tier,
)

# ---------------------------------------------------------------------------
# Domain vocabulary (K2: scoring)
# ---------------------------------------------------------------------------

WEIGHTS: Mapping[str, float] = {
    "error_rate": 0.20,
    "latency": 0.15,
    "saturation": 0.20,
    "dependency": 0.10,
    "freshness": 0.20,
    "entropy": 0.10,
    "trend": 0.05,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "weights must sum to 1.0"

ESCALATE_THRESHOLDS = {0.30: Level.Z2, 0.60: Level.Z3, 0.80: Level.Z4}

ALLOWED_BY_LEVEL = {
    Level.Z1: frozenset({ActionClass.OBSERVE, ActionClass.EXPAND}),
    Level.Z2: frozenset({ActionClass.OBSERVE, ActionClass.SNAPSHOT,
                         ActionClass.EXPAND, ActionClass.OPERATOR_ESCALATION}),
    Level.Z3: frozenset({ActionClass.OBSERVE, ActionClass.SNAPSHOT,
                         ActionClass.FREEZE_WRITES, ActionClass.QUARANTINE,
                         ActionClass.OPERATOR_ESCALATION}),
    Level.Z4: frozenset({ActionClass.OBSERVE, ActionClass.SNAPSHOT,
                         ActionClass.FREEZE_WRITES, ActionClass.QUARANTINE,
                         ActionClass.ROLLBACK, ActionClass.KILL_DISPOSABLE,
                         ActionClass.OPERATOR_ESCALATION}),
}

LADDER_RESILIENCE = Ladder(escalate=ESCALATE_THRESHOLDS, allowed=ALLOWED_BY_LEVEL)

STANDARD_SPECS = [
    ParamSpec("horizon_warn_age", Tier.D, 10.0),
    ParamSpec("horizon_beyond_age", Tier.D, 30.0),
    ParamSpec("damping_min_interval", Tier.D, 3.0),
    ParamSpec("deescalate_hold_cycles", Tier.D, 5.0),
    ParamSpec("learner_min_observations", Tier.C, 50.0),
    ParamSpec("learner_min_confidence", Tier.C, 0.6),
    ParamSpec("escalate_bias", Tier.B, 0.0),   # the only knob the learner may nudge sensitivity with
]


# ---------------------------------------------------------------------------
# K2: estimator
# ---------------------------------------------------------------------------

def estimator(signals: Iterable[Signal]) -> Score:
    """Weighted convolution of the module-13 features. Deterministic (I4):
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
    # Normalize over the features actually present: a missing sensor must not
    # look like "all clear" — but must not inflate the alarm either.
    return Score(value=total / used,
                 confidence=conf_sum / used,
                 features={n: features[n] for n in WEIGHTS if n in features})


def _confidence_of(signals: Iterable[Signal], name: str) -> float:
    for s in signals:
        if s.name == name:
            return max(0.0, min(1.0, s.confidence))
    return 0.0


# ---------------------------------------------------------------------------
# K6: learning loop (Gamma pattern, no τ-decay — lesson D1)
# ---------------------------------------------------------------------------

class ResilienceLearner:
    """Accumulates observations and proposes a sensitivity shift (Tier B).

    The constraints are enforced by the kernel, not taken on good faith:
    - escalate_bias — Tier B: the learner is entitled to it (governance lets it through);
    - provenance is required (governance rejects without it);
    - everything else (Tier C/D/E) the learner cannot touch — the attempt becomes
      a CHANGE_REJECTED audit event, not a crash of the circuit.
    """

    def __init__(self) -> None:
        self._observations: int = 0
        self._missed_escalations: int = 0   # episodes: score rose after we stayed at Z1

    def __call__(self, signals: Iterable[Signal],
                 memory: Mapping[str, float]) -> List[Change]:
        signals = list(signals)
        self._observations += 1
        n_min = int(memory.get("learner_min_observations", 50))
        conf_min = memory.get("learner_min_confidence", 0.6)

        score = estimator(signals)
        prev_score = memory.get("last_score", 0.0)
        if score.value > 0.60 >= prev_score:
            self._missed_escalations += 1

        if self._observations < n_min:
            return []                        # D1: no proposals before N_min
        if score.confidence < conf_min:
            return []                        # objectivity 0.6

        # Missed-escalation share > 20% — propose +0.05 sensitivity.
        if self._missed_escalations / max(1, self._observations) > 0.20:
            return [Change(param="escalate_bias", new_value=0.05,
                           author=AuthorRole.LEARNER,
                           provenance=f"obs={self._observations} "
                                      f"missed={self._missed_escalations}")]
        return []


# ---------------------------------------------------------------------------
# Plugin surface: what the conformance gate reads
# ---------------------------------------------------------------------------

class ResiliencePlugin:
    name = "resilience"

    def __init__(self) -> None:
        self.estimator = estimator
        self.learner = ResilienceLearner()


PLUGIN = ResiliencePlugin()
