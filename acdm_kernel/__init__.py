"""ACDM-KERNEL — the bare circuit architecture (K1–K7), invariants I1–I10 in code.

Patterns (resilience, security, cost...) bolt on as plugins through the
conformance gate. The kernel knows nothing about patterns.
"""
from .types import (
    ActionClass, ActionRequest, ActionResult, AuthorRole, Change, Decision,
    HorizonState, KernelViolation, Level, Score, Signal, Tier,
)
from .governance import Governance, ParamSpec
from .kernel import Kernel, Ladder, PluginFacade
from .conformance import ConformanceReport, run_conformance

__all__ = [
    "ActionClass", "ActionRequest", "ActionResult", "AuthorRole", "Change",
    "Decision", "HorizonState", "KernelViolation", "Level", "Score", "Signal",
    "Tier", "Governance", "ParamSpec", "Kernel", "Ladder", "PluginFacade",
    "ConformanceReport", "run_conformance",
]
