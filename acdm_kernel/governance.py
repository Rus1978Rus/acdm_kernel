"""ACDM-KERNEL · governance: the single door to parameters (I5/I6).

Invariants enforced HERE, not documented for the plugins to honor:
- an unknown parameter — KernelViolation (the registry is closed);
- Tier E — only with human_approved=True;
- LEARNER may change only Tier B/C, and only with provenance;
- roles (role:*) — Tier E: the learning loop CANNOT promote itself (I5);
- every accepted/rejected attempt is audited (by the calling kernel).
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
        """Register a spec (e.g. role:* on attach). Its default is active at once."""
        self._specs[spec.name] = spec
        self._values.setdefault(spec.name, spec.default)

    def value(self, name: str) -> float:
        return self._values[name]

    def get(self, name: str, default: float = 0.0) -> float:
        """The parameter's value, or default if it is not registered.

        Unlike value(), does not raise on a missing parameter — needed for
        optional cross-cutting kernel knobs (e.g. escalate_bias) that a given
        plugin may not define at all.
        """
        return self._values.get(name, default)

    def snapshot(self) -> Dict[str, float]:
        """A copy of all current values (to show the learning loop)."""
        return dict(self._values)

    def params_hash(self) -> str:
        return canonical_hash(self._values)

    def fresh(self) -> "Governance":
        """A new registry with the same specs and defaults (for probe kernels)."""
        return Governance(self._specs)

    def apply(self, change: Change) -> None:
        if change.param not in self._specs:
            raise KernelViolation(f"unknown param: {change.param}")
        spec = self._specs[change.param]

        if spec.tier is Tier.E and not change.human_approved:
            raise KernelViolation(f"{change.param}: Tier E requires human approval (I5)")

        if change.author is AuthorRole.LEARNER:
            if spec.tier >= Tier.D:
                raise KernelViolation(
                    f"{change.param}: Tier {spec.tier.name} exceeds the learner's authority (I6)")
            if not change.provenance:
                raise KernelViolation(f"{change.param}: learner without provenance (I6)")

        if change.author is AuthorRole.SYSTEM:
            raise KernelViolation("SYSTEM does not write parameters through governance")

        self._values[change.param] = change.new_value

    # Convenience factory for role parameters: assigning a role is constitutional (Tier E).
    @staticmethod
    def role_spec(plugin_name: str) -> ParamSpec:
        return ParamSpec(f"role:{plugin_name}", Tier.E, 0.0)
