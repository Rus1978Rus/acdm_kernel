"""ACDM-KERNEL · горизонт наблюдаемости (И8).

Ядро обязано знать, когда оно перестаёт видеть. Возраст сигналов сравнивается
с порогами из governance (Tier D — меняет только оператор):

- NORMAL:         все сигналы свежи;
- AT_HORIZON:     хотя бы один старше horizon_warn_age — доверие штрафуется;
- BEYOND_HORIZON: хотя бы один старше horizon_beyond_age — оценке НЕ ВЕРЯТ,
                  ядро принудительно уходит в Z4 и делает SNAPSHOT.

Идея ACDM-ST: black-box snapshot ДО потери наблюдаемости, не после.
"""
from __future__ import annotations

from typing import Iterable

from .types import HorizonState, Signal


class HorizonModel:
    def __init__(self, warn_age: float, beyond_age: float) -> None:
        if warn_age >= beyond_age:
            raise ValueError("warn_age должен быть строго меньше beyond_age")
        self.warn_age = warn_age
        self.beyond_age = beyond_age
        self.state = HorizonState.NORMAL
        self.oldest_age = 0.0

    def update(self, signals: Iterable[Signal], current_cycle: int) -> HorizonState:
        ages = [current_cycle - s.cycle for s in signals]
        self.oldest_age = max(ages) if ages else 0.0
        if self.oldest_age > self.beyond_age:
            self.state = HorizonState.BEYOND_HORIZON
        elif self.oldest_age > self.warn_age:
            self.state = HorizonState.AT_HORIZON
        else:
            self.state = HorizonState.NORMAL
        return self.state

    def confidence_factor(self) -> float:
        """Штраф к доверию оценки за старые данные (D3: явная формула, не символ)."""
        return {HorizonState.NORMAL: 1.0,
                HorizonState.AT_HORIZON: 0.5,
                HorizonState.BEYOND_HORIZON: 0.0}[self.state]
