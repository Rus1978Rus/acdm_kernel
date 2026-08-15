"""Эталонный плагин RESILIENCE для ACDM-KERNEL.

Прикручивает паттерн живучести (модули 01–13 спецификации ACDM-ST) к голому
ядру. Ядро поставляет контур и инварианты; плагин поставляет словарь домена:
какие сигналы читать, как их взвешивать, какие действия на каком уровне.

Что взято из спецификации — и что взято ИЗМЕНЁННЫМ, с указанием почему:

1. Веса z_score (модуль 13, верифицированы вычиткой, сумма = 1.00):
   error_rate 0.20, latency 0.15, saturation 0.20, dependency 0.10,
   freshness 0.20, entropy 0.10, trend 0.05.
   Машинная проверка суммы — в приёмочной батарее (урок D3: никаких
   неопределённых символов, каждая константа наблюдаема).

2. Пороги лестницы: Z2 при score ≥ 0.30, Z3 ≥ 0.60 (Z2.5 спецификации),
   Z4 ≥ 0.80.

3. Контур обучения: БЕЗ τ-decay. Урок D1 — τ_decay=60s против
   T_obs ≥ 24h делал обучение недостижимым: противоречие в константах,
   найденное вычиткой. Здесь вместо временного затухания — счётчик
   наблюдений (N_min = 50), что не зависит от wall-clock и сохраняет
   детерминизм (И4).

4. Доверие: предложения learner'а принимаются к рассмотрению только при
   confidence ≥ 0.6 (константа objectivity спецификации).

5. Чего плагин НЕ делает: не трогает executor, не пишет параметры напрямую,
   не назначает себе роль — всё это закрыто ядром, и conformance-гейт
   это проверяет до подключения (И10).
"""
from __future__ import annotations

from typing import Iterable, List, Mapping

from acdm_kernel import (
    ActionClass, AuthorRole, Change, Ladder, Level, ParamSpec, Score, Signal,
    Tier,
)

# ---------------------------------------------------------------------------
# Словарь домена (K2: оценка)
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
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "веса обязаны суммироваться в 1.0"

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
    ParamSpec("escalate_bias", Tier.B, 0.0),   # единственное, чем learner может «подкрутить» чувствительность
]


# ---------------------------------------------------------------------------
# K2: оценщик
# ---------------------------------------------------------------------------

def estimator(signals: Iterable[Signal]) -> Score:
    """Взвешенная свёртка признаков модели 13. Детерминирована (И4):
    никакого random, никакого wall-clock."""
    features = {s.name: s.value for s in signals}
    total, conf_sum, used = 0.0, 0.0, 0.0
    for name, w in WEIGHTS.items():
        if name in features:
            total += w * max(0.0, min(1.0, features[name]))
            conf_sum += w * _confidence_of(signals, name)
            used += w
    if used == 0.0:
        return Score(value=0.0, confidence=0.0, features={})
    # Нормировка на фактически присутствующие признаки: отсутствие датчика
    # не должно выглядеть как «всё хорошо» — но и не должно раздувать тревогу.
    return Score(value=total / used,
                 confidence=conf_sum / used,
                 features={n: features[n] for n in WEIGHTS if n in features})


def _confidence_of(signals: Iterable[Signal], name: str) -> float:
    for s in signals:
        if s.name == name:
            return max(0.0, min(1.0, s.confidence))
    return 0.0


# ---------------------------------------------------------------------------
# K6: контур обучения (Gamma-паттерн, без τ-decay — урок D1)
# ---------------------------------------------------------------------------

class ResilienceLearner:
    """Накапливает наблюдения и предлагает сдвиг чувствительности (Tier B).

    Ограничения исполняются ядром, а не честным словом:
    - escalate_bias — Tier B: learner вправе (governance пропустит);
    - provenance обязателен (governance отклонит без него);
    - всё остальное (Tier C/D/E) learner не может — попытка станет
      событием CHANGE_REJECTED в аудите, не падением контура.
    """

    def __init__(self) -> None:
        self._observations: int = 0
        self._missed_escalations: int = 0   # эпизоды: score рос после того, как мы остались на Z1

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
            return []                        # D1: раньше N_min предложений нет
        if score.confidence < conf_min:
            return []                        # objectivity 0.6

        # Доля пропущенных эскалаций > 20% — предлагаем +0.05 чувствительности.
        if self._missed_escalations / max(1, self._observations) > 0.20:
            return [Change(param="escalate_bias", new_value=0.05,
                           author=AuthorRole.LEARNER,
                           provenance=f"obs={self._observations} "
                                      f"missed={self._missed_escalations}")]
        return []


# ---------------------------------------------------------------------------
# Поверхность плагина: то, что читает conformance-гейт
# ---------------------------------------------------------------------------

class ResiliencePlugin:
    name = "resilience"

    def __init__(self) -> None:
        self.estimator = estimator
        self.learner = ResilienceLearner()


PLUGIN = ResiliencePlugin()
