"""ACDM-KERNEL · аудит-хребет (И3).

Append-only журнал с хеш-цепочкой: каждое событие содержит хеш предыдущего.
Подмена или вырезание события ломает цепочку — verify() это видит.

Правила:
- записи только добавляются (нет update/delete API в принципе);
- время — логический цикл, не wall-clock (детерминизм, И4);
- И2 упорядочивание: INTENT действия пишется ДО исполнения, OUTCOME — после.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    cycle: int
    kind: str                 # DECISION / INTENT / OUTCOME / CHANGE / CHANGE_REJECTED / ATTACH / HORIZON
    payload: Any
    prev_hash: str
    event_hash: str

    @staticmethod
    def make(seq: int, cycle: int, kind: str, payload: Any, prev_hash: str) -> "AuditEvent":
        blob = json.dumps(
            {"seq": seq, "cycle": cycle, "kind": kind, "payload": payload, "prev": prev_hash},
            sort_keys=True, separators=(",", ":"), default=str,
        )
        return AuditEvent(seq, cycle, kind, payload, prev_hash,
                          hashlib.sha256(blob.encode("utf-8")).hexdigest())


class AuditSpine:
    GENESIS = "0" * 64

    def __init__(self) -> None:
        self._events: List[AuditEvent] = []

    def append(self, cycle: int, kind: str, payload: Any) -> AuditEvent:
        prev = self._events[-1].event_hash if self._events else self.GENESIS
        ev = AuditEvent.make(len(self._events), cycle, kind, payload, prev)
        self._events.append(ev)
        return ev

    def events(self) -> List[AuditEvent]:
        return list(self._events)          # копия: снаружи журнал не мутируют

    def verify(self) -> bool:
        prev = self.GENESIS
        for i, ev in enumerate(self._events):
            if ev.seq != i or ev.prev_hash != prev:
                return False
            if ev != AuditEvent.make(ev.seq, ev.cycle, ev.kind, ev.payload, ev.prev_hash):
                return False
            prev = ev.event_hash
        return True

    def __len__(self) -> int:
        return len(self._events)
