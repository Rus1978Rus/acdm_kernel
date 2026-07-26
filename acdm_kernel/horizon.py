"""ACDM-KERNEL · observability horizon (I8).

The kernel must know when it stops seeing. Signal age is compared against
thresholds from governance (Tier D — operator-only):

- NORMAL:         every signal is fresh;
- AT_HORIZON:     at least one is older than horizon_warn_age — confidence is penalized;
- BEYOND_HORIZON: at least one is older than horizon_beyond_age — the estimate is
                  NOT TRUSTED; the kernel is forced into Z4 and takes a SNAPSHOT.

The ACDM-ST idea: a black-box snapshot BEFORE observability is lost, not after.
"""
from __future__ import annotations

from typing import Iterable

from .types import HorizonState, Signal


class HorizonModel:
    def __init__(self, warn_age: float, beyond_age: float) -> None:
        if warn_age >= beyond_age:
            raise ValueError("warn_age must be strictly less than beyond_age")
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
        """Confidence penalty for stale data (D3: an explicit formula, not a symbol)."""
        return {HorizonState.NORMAL: 1.0,
                HorizonState.AT_HORIZON: 0.5,
                HorizonState.BEYOND_HORIZON: 0.0}[self.state]
