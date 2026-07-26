"""ACDM-KERNEL · контур.

Композиция семи элементов (K1–K7). Инварианты, которые здесь исполняются
кодом, а не обещаются текстом:

- И1: исполнитель приватен. Плагин физически не может вызвать актуатор
  напрямую — у PluginFacade нет такого атрибута.
- И2: порядок аудита — INTENT до исполнения, OUTCOME после.
- И3: аудит append-only с хеш-цепочкой (audit.py).
- И4: детерминизм — логические циклы, канонические хеши; одинаковый вход
  даёт одинаковый Decision + одинаковый аудит.
- И5/И6: запись параметров только через governance (governance.py).
- И7: лестница уровней — действие вне допуска уровня отклоняется ДО
  исполнителя.
- И8: BEYOND_HORIZON принудительно переводит контур в Z4 и заставляет
  SNAPSHOT — независимо от того, что «думает» estimator плагина.
- И9: демпфер и удержание (damping.py); эскалация никогда не блокируется.
- И10: плагин подключается только после прохождения conformance-гейта
  на ИЗОЛИРОВАННОМ probe-ядре — проверка не мутирует живой контур.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Callable, Dict, Iterable, Mapping, Optional

from .audit import AuditSpine
from .damping import Damper, EscalationHold
from .governance import Governance, ParamSpec
from .horizon import HorizonModel
from .types import (
    ActionClass, ActionRequest, ActionResult, AuthorRole, Change, Decision,
    HorizonState, KernelViolation, Level, Score, Signal, Tier, canonical_hash,
)


class Ladder:
    """И7: какой уровень какие классы действий допускает."""

    def __init__(self, escalate: Mapping[float, Level],
                 allowed: Mapping[Level, frozenset]) -> None:
        self._escalate = dict(sorted(escalate.items()))  # порог -> уровень
        self._allowed = dict(allowed)

    def level_for(self, score: Score) -> Level:
        level = Level.Z1
        for threshold, lv in self._escalate.items():
            if score.value >= threshold:
                level = lv
        return level

    def permits(self, level: Level, action: ActionClass) -> bool:
        return action in self._allowed[level]


class _Executor:
    """K4, приватный (И1). Единственный путь к актуатору — через Kernel.execute."""

    def __init__(self, actuator: Optional[Callable[[ActionRequest], str]] = None) -> None:
        self._locks: Dict[str, ActionClass] = {}
        self._actuator = actuator or (lambda req: "APPLIED")

    def do(self, request: ActionRequest, cycle: int) -> ActionResult:
        return ActionResult(request=request, status=self._actuator(request), cycle=cycle)

    def lock(self, scope: str, action: ActionClass) -> bool:
        if scope in self._locks:
            return False
        self._locks[scope] = action
        return True

    def locks(self) -> Mapping[str, ActionClass]:
        return MappingProxyType(self._locks)


class PluginFacade:
    """Всё, что плагин МОЖЕТ видеть и делать. Роль привязана при attach (И1).

    У фасада намеренно нет атрибутов executor / governance / audit — проверяется
    conformance-гейтом (T1).
    """

    def __init__(self, kernel: "Kernel", name: str, role: AuthorRole) -> None:
        object.__setattr__(self, "_kernel", kernel)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_role", role)

    def __setattr__(self, key, value):  # фасад неизменяем снаружи
        raise KernelViolation(f"facade read-only: {key}")

    @property
    def role(self) -> AuthorRole:
        return self._role

    def request_action(self, request: ActionRequest) -> ActionResult:
        return self._kernel.execute(self._name, request)

    def read_memory(self) -> Mapping[str, float]:
        return self._kernel.memory_view()

    def propose_change(self, change: Change) -> None:
        if change.author is not self._role:
            raise KernelViolation("фасад пишет только от своей роли (И5)")
        self._kernel.apply_change(change)


class Kernel:
    """K1–K7 в одном контуре. Время — целые логические циклы (И4)."""

    def __init__(self, *, specs: Mapping[str, ParamSpec], ladder: Ladder,
                 actuator: Optional[Callable[[ActionRequest], str]] = None) -> None:
        self._specs = dict(specs)
        self._gov = Governance(self._specs)
        self._ladder = ladder
        self._audit = AuditSpine()
        self._horizon = HorizonModel(
            warn_age=self._gov.value("horizon_warn_age"),
            beyond_age=self._gov.value("horizon_beyond_age"),
        )
        self._damper = Damper()
        self._hold = EscalationHold()
        self._executor = _Executor(actuator)
        self._memory: Dict[str, float] = {}
        self._level = Level.Z1
        self._plugins: Dict[str, PluginFacade] = {}

    # ---------- наблюдаемость для внешнего мира (только чтение) ----------

    @property
    def level(self) -> Level:
        return self._level

    def memory_view(self) -> Mapping[str, float]:
        return MappingProxyType(self._memory)   # И3: память снаружи не мутируют

    def audit_events(self):
        return self._audit.events()

    def audit_ok(self) -> bool:
        return self._audit.verify()

    # ---------- главный цикл (K1→K2→K3, с K5/K6/K7 внутри) ----------

    def cycle(self, signals: Iterable[Signal], estimator,
              learner=None, cycle: int = 0) -> Decision:
        signals = list(signals)

        # И8: сначала горизонт — доверие оценке зависит от него.
        hstate = self._horizon.update(signals, cycle)
        if hstate is not HorizonState.NORMAL:
            self._audit.append(cycle, "HORIZON",
                               {"state": hstate.name, "oldest_age": self._horizon.oldest_age})

        score = estimator(signals)
        score = Score(value=score.value,
                      confidence=score.confidence * self._horizon.confidence_factor(),
                      features=score.features)

        level = self._ladder.level_for(score)
        if hstate is HorizonState.BEYOND_HORIZON:
            level = Level.Z4                    # И8: оценке не верим

        self._level = Level(self._hold.check(
            int(self._level), int(level), cycle,
            hold_cycles=int(self._gov.value("deescalate_hold_cycles"))))

        if hstate is HorizonState.BEYOND_HORIZON:
            # Black-box snapshot ДО потери наблюдаемости; forced — урок 13B.
            self.execute("__kernel__", ActionRequest(
                ActionClass.SNAPSHOT, scope="__kernel__",
                reason="BEYOND_HORIZON: слепое аварийное состояние"),
                forced=True, author=AuthorRole.SYSTEM)

        if learner is not None:
            for change in learner(signals, self._memory):
                try:
                    self.apply_change(change)
                except KernelViolation as exc:
                    # нарушение контура обучения — событие аудита, не падение контура
                    self._audit.append(cycle, "CHANGE_REJECTED",
                                       {"param": change.param, "reason": str(exc)})

        decision = Decision(level=self._level, score=score, horizon=hstate,
                            memory_state_hash=canonical_hash(self._memory),
                            cycle=cycle)
        self._audit.append(cycle, "DECISION", {
            "level": self._level.name, "score": score.value,
            "confidence": score.confidence, "horizon": hstate.name,
            "memory_hash": decision.memory_state_hash})
        return decision

    # ---------- действия (K4 через И2/И7/И9) ----------

    def execute(self, plugin_name: str, request: ActionRequest, *,
                cycle: int = 0, forced: bool = False,
                author: AuthorRole = AuthorRole.LEARNER) -> ActionResult:
        if not self._ladder.permits(self._level, request.action):
            result = ActionResult(request, "BLOCKED_BY_LEVEL", cycle)
            self._audit.append(cycle, "OUTCOME", {
                "by": plugin_name, "action": request.action.name,
                "scope": request.scope, "status": result.status})
            return result

        damping = int(self._gov.value("damping_min_interval"))
        if not self._damper.allow(request, damping, cycle, forced=forced):
            result = ActionResult(request, "BLOCKED_BY_DAMPING", cycle)
            self._audit.append(cycle, "OUTCOME", {
                "by": plugin_name, "action": request.action.name,
                "scope": request.scope, "status": result.status})
            return result

        # И2: INTENT до исполнения.
        self._audit.append(cycle, "INTENT", {
            "by": plugin_name, "action": request.action.name,
            "scope": request.scope, "reason": request.reason,
            "author": author.value, "params_hash": self._gov.params_hash()})

        result = self._executor.do(request, cycle)

        self._audit.append(cycle, "OUTCOME", {
            "by": plugin_name, "action": request.action.name,
            "scope": request.scope, "status": result.status})
        return result

    # ---------- параметры (K6 через governance) ----------

    def apply_change(self, change: Change) -> None:
        self._gov.apply(change)               # бросает KernelViolation при нарушении
        self._audit.append(change.provenance_cycle if hasattr(change, "provenance_cycle") else 0,
                           "CHANGE", {"param": change.param, "value": change.new_value,
                                      "author": change.author.value,
                                      "provenance": change.provenance,
                                      "human_approved": change.human_approved})

    # ---------- подключение плагинов (И10) ----------

    def _spawn_probe(self) -> "Kernel":
        """Изолированная копия контура для conformance-гейта.

        Проверки плагина НЕ ДОЛЖНЫ мутировать живое ядро: ни уровень, ни аудит,
        ни параметры. Probe получает свежий governance с теми же спецификациями.
        """
        return Kernel(specs=self._specs, ladder=self._ladder)  # свежие дефолты

    def attach(self, name: str, plugin, role: AuthorRole = AuthorRole.LEARNER) -> PluginFacade:
        from .conformance import run_conformance    # локальный импорт: без цикла

        report = run_conformance(self._spawn_probe, plugin, role)
        if not report.passed:
            raise KernelViolation(f"conformance не пройден (И10): {report.failures}")
        # Назначение роли — конституция: role:{name} регистрируется как Tier E (И5).
        role_spec = Governance.role_spec(name)
        self._specs[role_spec.name] = role_spec
        self._gov.add_spec(role_spec)
        facade = PluginFacade(self, name, role)
        self._plugins[name] = facade
        self._audit.append(0, "ATTACH", {"plugin": name, "role": role.value})
        return facade

    def _make_test_facade(self, plugin_name: str, role: AuthorRole) -> PluginFacade:
        """Фасад для conformance-проверок поверхности (T1). Не регистрируется."""
        return PluginFacade(self, plugin_name, role)
