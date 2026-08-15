"""ACDM-KERNEL · типы контура.

Доменно-нейтральные структуры. Словарь паттернов (blast radius, pressure,
агент, перерасход...) сюда НЕ попадает — он живёт в плагинах.

Логические часы вместо wall-time: детерминизм (И4) требует, чтобы одинаковый
вход давал одинаковый результат включая хеши аудита.
"""
from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


class Level(enum.IntEnum):
    """Лестница деградации (И7). Порядок значений = порядок строгости."""
    Z1 = 1   # нормальное наблюдение
    Z2 = 2   # усиленное наблюдение
    Z3 = 3   # сдерживание
    Z4 = 4   # аварийное сдерживание


class HorizonState(enum.IntEnum):
    """Модель собственной наблюдаемости (И8)."""
    NORMAL = 0
    AT_HORIZON = 1
    BEYOND_HORIZON = 2


class ActionClass(enum.Enum):
    OBSERVE = "OBSERVE"
    SNAPSHOT = "SNAPSHOT"
    FREEZE_WRITES = "FREEZE_WRITES"
    QUARANTINE = "QUARANTINE"
    ROLLBACK = "ROLLBACK"
    KILL_DISPOSABLE = "KILL_DISPOSABLE"
    EXPAND = "EXPAND"                    # рост: запрещён на высоких уровнях
    OPERATOR_ESCALATION = "OPERATOR_ESCALATION"


class Tier(enum.IntEnum):
    """Тиры управления изменениями (И5/И6). E — только человек."""
    B = 1   # среда: может менять контур обучения (с provenance)
    C = 2   # статистика: контур обучения (с provenance)
    D = 3   # политика: только оператор
    E = 4   # конституция: только человек, только с явным approval


class AuthorRole(enum.Enum):
    SYSTEM = "system"     # само ядро (внутренние изменения, не через governance)
    HUMAN = "human"       # операторская консоль
    LEARNER = "learner"   # контур обучения (Gamma-паттерн)


class KernelViolation(PermissionError):
    """Попытка нарушить инвариант. Единственный ответ ядра — это исключение."""


@dataclass(frozen=True)
class Signal:
    name: str
    value: float
    confidence: float
    cycle: int            # логические часы производства сигнала


@dataclass(frozen=True)
class Score:
    value: float
    confidence: float
    features: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionRequest:
    action: ActionClass
    scope: str
    reason: str


@dataclass(frozen=True)
class ActionResult:
    request: ActionRequest
    status: str           # APPLIED / BLOCKED_BY_LEVEL / BLOCKED_BY_LOCK / BLOCKED_BY_DAMPING
    cycle: int


@dataclass(frozen=True)
class Change:
    """Предложение изменения параметра. Другого пути записи параметров нет."""
    param: str
    new_value: float
    author: AuthorRole
    provenance: str       # обязателен для LEARNER: откуда предложение
    human_approved: bool = False


@dataclass(frozen=True)
class Decision:
    level: Level
    score: Score
    horizon: HorizonState
    memory_state_hash: str
    cycle: int


def canonical_hash(obj: Any) -> str:
    """Канонический хеш состояния (сортировка ключей, стабильные float)."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
