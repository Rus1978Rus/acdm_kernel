"""ACDM-KERNEL · демпфирование и удержание уровня (И9).

Два урока спецификации, исполненные в коде:

- Урок 13B: демпфер НЕ ДОЛЖЕН блокировать безопасность. Поэтому у каждого
  вызова есть forced=True — для действий, которые выбирает само ядро
  (SNAPSHOT при BEYOND_HORIZON). Демпфер защищает от флапа плагинов,
  а не от собственных защитных реакций контура.

- Урок D2: квоты на эскалацию — ошибка. Эскалация НИКОГДА не блокируется.
  Демпфер применяется только к повторам ОДНОГО действия в ОДНОМ scope
  (anti-flap), а удержание — только к ДЕ-эскалации.
"""
from __future__ import annotations

from typing import Dict, Tuple

from .types import ActionClass, ActionRequest


class Damper:
    """Минимальный интервал между повторами (action, scope). Не блокирует новые scope."""

    def __init__(self) -> None:
        self._last: Dict[Tuple[ActionClass, str], int] = {}

    def allow(self, request: ActionRequest, min_interval: int, cycle: int,
              forced: bool = False) -> bool:
        if forced:
            return True                      # урок 13B: безопасность не демпфируется
        key = (request.action, request.scope)
        last = self._last.get(key)
        if last is not None and cycle - last < min_interval:
            return False
        self._last[key] = cycle
        return True


class EscalationHold:
    """Де-эскалация требует deescalate_hold_cycles стабильных циклов.

    Эскалация — мгновенно, всегда (урок D2).
    """

    def __init__(self) -> None:
        self._stable_since: int | None = None
        self._candidate = None

    def check(self, current_level: int, wants_level: int, cycle: int,
              hold_cycles: int) -> int:
        if wants_level > current_level:
            self._stable_since = None
            self._candidate = None
            return wants_level               # эскалация мгновенна
        if wants_level < current_level:
            if self._candidate != wants_level:
                self._candidate = wants_level
                self._stable_since = cycle
                return current_level
            if cycle - self._stable_since < hold_cycles:
                return current_level         # держим: стабильность ещё не доказана
            self._stable_since = None
            self._candidate = None
            return wants_level
        return current_level
