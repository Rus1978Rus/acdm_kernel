"""ACDM-KERNEL · governance: единственная дверь к параметрам (И5/И6).

Инварианты, исполняемые ЗДЕСЬ, а не документируемые для плагинов:
- неизвестный параметр — KernelViolation (реестр закрыт);
- Tier E — только с human_approved=True;
- LEARNER может менять только Tier B/C и только с provenance;
- роли (role:*) — Tier E: контур обучения НЕ МОЖЕТ повысить себя (И5);
- каждая удавшаяся/отвергнутая попытка уходит в аудит (вызывающим ядром).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping

from .types import AuthorRole, Change, KernelViolation, Tier, canonical_hash


@dataclass(frozen=True)
class ParamSpec:
    name: str
    tier: Tier
    default: float


class Governance:
    def __init__(self, specs: Mapping[str, ParamSpec]) -> None:
        self._specs: Dict[str, ParamSpec] = dict(specs)
        self._values: Dict[str, float] = {n: s.default for n, s in specs.items()}

    def add_spec(self, spec: ParamSpec) -> None:
        """Регистрация спецификации (например role:* при attach). Дефолт активен сразу."""
        self._specs[spec.name] = spec
        self._values.setdefault(spec.name, spec.default)

    def value(self, name: str) -> float:
        return self._values[name]

    def get(self, name: str, default: float = 0.0) -> float:
        """Значение параметра или default, если он не зарегистрирован.

        В отличие от value(), не бросает на отсутствующем параметре — нужно для
        опциональных общих регуляторов ядра (например escalate_bias), которых
        у конкретного плагина может и не быть.
        """
        return self._values.get(name, default)

    def snapshot(self) -> Dict[str, float]:
        """Копия всех текущих значений (для показа контуру обучения)."""
        return dict(self._values)

    def params_hash(self) -> str:
        return canonical_hash(self._values)

    def fresh(self) -> "Governance":
        """Новый реестр с теми же спецификациями и дефолтами (для probe-ядер)."""
        return Governance(self._specs)

    def apply(self, change: Change) -> None:
        if change.param not in self._specs:
            raise KernelViolation(f"unknown param: {change.param}")
        spec = self._specs[change.param]

        if spec.tier is Tier.E and not change.human_approved:
            raise KernelViolation(f"{change.param}: Tier E требует одобрения человека (И5)")

        if change.author is AuthorRole.LEARNER:
            if spec.tier >= Tier.D:
                raise KernelViolation(
                    f"{change.param}: Tier {spec.tier.name} выше полномочий обучения (И6)")
            if not change.provenance:
                raise KernelViolation(f"{change.param}: learner без provenance (И6)")

        if change.author is AuthorRole.SYSTEM:
            raise KernelViolation("SYSTEM не пишет параметры через governance")

        self._values[change.param] = change.new_value

    # Удобные фабрики ролевых параметров: назначение роли — конституция (Tier E).
    @staticmethod
    def role_spec(plugin_name: str) -> ParamSpec:
        return ParamSpec(f"role:{plugin_name}", Tier.E, 0.0)
