"""ACDM-KERNEL — голая архитектура контура (K1–K7), инварианты И1–И10 в коде.

Паттерны (живучесть, безопасность, стоимость...) прикручиваются плагинами
через conformance-гейт. Ядро о паттернах ничего не знает.
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
