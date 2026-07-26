"""Плагин AI-AGENT-SUPERVISOR для ACDM-KERNEL.

Прикручивает к голому ядру надзор за автономным ИИ-агентом: «стоп-кран» с
градуированной реакцией и несгораемым журналом под регулятора (EU AI Act,
ISO 42001). Ядро поставляет контур и инварианты; плагин поставляет словарь
домена: какие сигналы поведения агента читать, как их взвешивать, какие
защитные действия разрешены на каком уровне тревоги.

Отличия от эталонного resilience — сознательные и обоснованные:

1. Веса сигналов (сумма = 1.00, ядро проверяет). Домен — не инфраструктура,
   а поведение агента: чаще всего покупателя пугает «агент лезет туда, куда
   не положено» и «дрейф вывода», поэтому permission_denials и output_anomaly
   получают наибольший вес (по 0.20).

2. НЕТ контура обучения — сознательно, как продуктовое решение для v1.
   (Методологический аудит находил петлю обучения ядра разомкнутой; теперь она
   в ядре починена — escalate_bias читается, живая память заполняется. Но давать
   надзирателю за ИИ автоматически подкручивать собственную чувствительность в
   первой версии рискованно: цена ошибки высокая, поведение должно быть
   предсказуемым для регулятора.) Поэтому здесь learner нужен ровно для ОДНОГО:
   продемонстрировать ГАРДРЕЙЛ — агент не может повысить сам себе полномочия.
   Попытка агента поднять свой бюджет (Tier D) отвергается governance и уходит в
   аудит событием CHANGE_REJECTED. Это и есть продающая строка: «ИИ не может сам
   себя разблокировать» — вшито, а не обещано.

3. Ключевой параметр agent_autonomy — Tier E (конституция): вернуть агенту
   автономию после заморозки может только человек и только с явным одобрением
   (human_approved=True). Это запись «человек-в-контуре» для регулятора.
"""
from __future__ import annotations

from typing import Iterable, List, Mapping

from acdm_kernel import (
    ActionClass, AuthorRole, Change, Ladder, Level, ParamSpec, Score, Signal,
    Tier,
)

# ---------------------------------------------------------------------------
# Словарь домена (K2: оценка поведения агента)
# ---------------------------------------------------------------------------

WEIGHTS: Mapping[str, float] = {
    "error_rate": 0.15,            # доля неуспешных/отклонённых действий агента
    "permission_denials": 0.20,    # как часто агент лезет туда, куда не положено
    "cost_burn": 0.15,             # скорость перерасхода бюджета (токены/деньги/вызовы)
    "loop_rate": 0.10,             # зацикливание, аномально длинные цепочки
    "output_anomaly": 0.20,        # дрейф/аномалия вывода (внешний детектор -> число)
    "safety_flags": 0.10,          # срабатывания фильтров (утечка PII, небезопасный контент)
    "human_override_rate": 0.10,   # как часто люди отменяют действия агента
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "веса обязаны суммироваться в 1.0"

# Пороги лестницы тревоги: Z2 при score >= 0.30, Z3 >= 0.60, Z4 >= 0.80.
ESCALATE_THRESHOLDS = {0.30: Level.Z2, 0.60: Level.Z3, 0.80: Level.Z4}

# Действия по уровням в переводе на надзор за ИИ-агентом:
#   Z1 — норма, можно давать больше автономии (EXPAND);
#   Z2 — снапшот контекста + уведомить оператора;
#   Z3 — отобрать опасные инструменты (FREEZE_WRITES = read-only),
#        изолировать сессию (QUARANTINE);
#   Z4 — полный стоп: откат последних действий (ROLLBACK), убить агента
#        (KILL_DISPOSABLE), чёрный ящик.
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

LADDER_AI_AGENT = Ladder(escalate=ESCALATE_THRESHOLDS, allowed=ALLOWED_BY_LEVEL)

STANDARD_SPECS = [
    ParamSpec("horizon_warn_age", Tier.D, 10.0),
    ParamSpec("horizon_beyond_age", Tier.D, 30.0),
    ParamSpec("damping_min_interval", Tier.D, 3.0),
    ParamSpec("deescalate_hold_cycles", Tier.D, 4.0),
    # Бюджет агента — Tier D: меняет только оператор. Сам агент (роль LEARNER)
    # поднять его не может — governance отвергнет, аудит зафиксирует.
    ParamSpec("agent_budget_limit", Tier.D, 100.0),
    # Автономия агента — Tier E (конституция): вернуть после заморозки может
    # только человек и только с явным approval. Это «человек-в-контуре».
    ParamSpec("agent_autonomy", Tier.E, 1.0),
]


# ---------------------------------------------------------------------------
# K2: оценщик поведения агента
# ---------------------------------------------------------------------------

def estimator(signals: Iterable[Signal]) -> Score:
    """Взвешенная свёртка сигналов поведения агента. Детерминирована (И4):
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
    # Нормировка на фактически присутствующие сигналы: отсутствие датчика не
    # должно выглядеть как «всё хорошо», но и не должно раздувать тревогу.
    return Score(value=total / used,
                 confidence=conf_sum / used,
                 features={n: features[n] for n in WEIGHTS if n in features})


def _confidence_of(signals: Iterable[Signal], name: str) -> float:
    for s in signals:
        if s.name == name:
            return max(0.0, min(1.0, s.confidence))
    return 0.0


# ---------------------------------------------------------------------------
# K6: гардрейл полномочий (НЕ самообучение)
# ---------------------------------------------------------------------------

class BudgetGuardrail:
    """Ролевая «просьба» агента — и доказательство, что она не проходит.

    Когда агент упирается в лимит бюджета, он от роли LEARNER предлагает
    поднять свой agent_budget_limit. Это Tier D — выше полномочий обучения.
    Governance отвергает попытку, а ядро записывает CHANGE_REJECTED в аудит.
    Так гардрейл «ИИ не повышает сам себе полномочия» доказывается замером,
    а не обещанием (И5/И6).
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
                           provenance="agent: бюджет исчерпан, запрос на повышение")]
        return []


# ---------------------------------------------------------------------------
# Поверхность плагина: то, что читает conformance-гейт (И10)
# ---------------------------------------------------------------------------

class AIAgentSupervisorPlugin:
    name = "ai-agent"

    def __init__(self) -> None:
        self.estimator = estimator
        self.learner = BudgetGuardrail()


PLUGIN = AIAgentSupervisorPlugin()
