# ACDM-KERNEL — bare architecture in code

A seven-element circuit (K1–K7) where invariants I1–I10 are **enforced by code**,
not documented for operators to follow. Patterns (resilience, security, cost,
compliance) bolt on as plugins through a conformance gate. The kernel knows
nothing about patterns; a plugin need not know the mechanics of the circuit.

```
acdm_kernel/            kernel (domain-neutral)
  types.py             Signal / Score / Change / Level / Tier / KernelViolation
  audit.py             append-only spine with a hash chain          (I2, I3)
  governance.py        the single door to parameters                (I5, I6)
  horizon.py           model of the kernel's own observability      (I8)
  damping.py           anti-flap + de-escalation hold               (I9)
  kernel.py            circuit K1–K7, the ladder, the plugin facade (I1, I4, I7)
  conformance.py       attach gate on an isolated probe             (I10)
patterns/resilience/   reference resilience plugin (spec modules 01–13)
patterns/ai_agent/     AI-agent supervisor plugin (kill-switch + regulator-ready audit)
examples/              ai_agent_demo.py · build_dashboard.py (+ dashboard.html)
tests/                 test_battery.py (27) · test_ai_agent.py (14) · test_wiring.py (8)
legacy/                frozen Russian original (self-contained, runs on its own)
```

## Map: invariant → code → battery measurement

| Invariant | Where it is enforced | How it is measured |
|---|---|---|
| I1 executor unreachable to the plugin | `_Executor` is private; `PluginFacade` has no such attribute | conformance T1 |
| I2 INTENT before execution, OUTCOME after | `Kernel.execute` | C2 |
| I3 audit append-only, tamper-evident | `AuditSpine` hash chain | C1, C3 (forgery is caught) |
| I4 determinism (logical cycles, no wall-clock) | `Signal.cycle`, `canonical_hash` | B1, B2; conformance T2 |
| I5 the learning loop cannot promote itself | `role:*` — Tier E | F4; conformance T5 |
| I6 authority tiers B/C/D/E | `Governance.apply` | F1–F6; conformance T3 |
| I7 an action outside the level's clearance is rejected before the executor | `Ladder.permits` | D2 |
| I8 blindness → Z4 + black-box SNAPSHOT, regardless of estimator | `Kernel.cycle` + `HorizonModel` | E1–E3; conformance T4 |
| I9 escalation is instant and unblockable; de-escalation is held | `EscalationHold`, `Damper(forced=)` | D3–D6 |
| I10 no attach without the conformance gate | `Kernel.attach` → probe kernel | G1–G4 |

## Spec lessons baked into the code

- **D1 (Gamma unreachable)**: τ_decay=60s against T_obs≥24h — a contradiction in
  the constants, caught by proofreading. Here: an observation counter `N_min=50`
  instead of time decay → reachability proven by measurement H1.
- **D2 (escalation quota)**: rejected. `EscalationHold` never blocks a level
  increase — it only slows a decrease.
- **13B (damper vs safety)**: `Damper.allow` has `forced=True` for the kernel's
  own actions (SNAPSHOT on BEYOND_HORIZON).
- **D3 (undefined symbols χ, freshness_factor)**: every constant is either an
  observable governance parameter or an explicit formula with a machine check
  (A1: "the weights sum to 1.00" is checked, not trusted).

## How to bolt on your own pattern

```python
from acdm_kernel import Kernel, Level, ActionClass
from patterns.resilience.plugin import STANDARD_SPECS, LADDER_RESILIENCE, PLUGIN

kernel = Kernel(specs={s.name: s for s in STANDARD_SPECS}, ladder=LADDER_RESILIENCE)
facade = kernel.attach("resilience", PLUGIN)   # conformance gate inside (I10)

decision = kernel.cycle(signals, PLUGIN.estimator, PLUGIN.learner, cycle=0)
result = facade.request_action(ActionRequest(ActionClass.FREEZE_WRITES, "db", "reason"))
```

A plugin must provide: `name`, a deterministic `estimator(signals) -> Score`.
Optionally: `learner(signals, memory) -> [Change]`. Everything else is the kernel.

A new domain = a new plugin: its own features and weights in the estimator, its
own clearance ladder `Ladder(escalate=..., allowed=...)`, its own ParamSpec. The
kernel does not change. See `patterns/ai_agent/` for a second, non-infrastructure
domain — oversight of an autonomous AI agent — on the same unchanged kernel.

## See it run

```
python3 examples/ai_agent_demo.py      # replays one incident, prints the regulator-ready trace
python3 examples/build_dashboard.py    # generates examples/dashboard.html (Control panel + Audit log)
```

## Honest boundaries

- **Reference implementation, not production**: one process, in-RAM memory,
  non-persistent audit. The hash chain proves log integrity within the process,
  not against an external adversary with access to the process.
- **The actuator is a stub**: `Kernel(actuator=fn)` is the attach point for real
  executors; the kernel guarantees only clearance and logging, not the physical
  action itself.
- **Horizon by signal age** — the simplest observability model; the spec knows
  8 states, here 3 (NORMAL/AT/BEYOND) — lesson N3 is honored as an explicit,
  checkable mapping, not "somewhere between the modules".
- **Determinism (I4)** means: the same sequence of cycles and signals → the same
  decisions and audit. Real time is deliberately banished.

## Running the acceptance battery

```
python3 tests/test_battery.py     # "ALL GREEN — 27 checks passed" — or do not ship
python3 tests/test_ai_agent.py    # AI-agent supervisor — 14 checks
python3 tests/test_wiring.py      # findings A/B/D wiring regression — 8 checks
```

---

The original Russian implementation is preserved, frozen and self-contained,
under [`legacy/`](legacy/) for provenance.
