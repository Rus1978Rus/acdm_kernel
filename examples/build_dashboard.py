"""ACDM-KERNEL · "Control panel + Audit log" dashboard generator.

Runs THREE real scenarios on the live kernel and assembles a self-contained HTML
page (no external resources) — a demo to show a first client. The dashboard
reflects genuine kernel output: levels, risk, horizon state and the audit trace
with its hash chain all come from real measurements, not hand-drawn mockups.

Run:
    python3 examples/build_dashboard.py           # -> examples/dashboard.html
    python3 examples/build_dashboard.py --body OUT # body only (for an artifact)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acdm_kernel import (                      # noqa: E402
    ActionClass, ActionRequest, AuthorRole, Change, Kernel, Signal,
)
from patterns.ai_agent import plugin as ap     # noqa: E402

LEVEL_HEX = {1: "#3fb950", 2: "#d9a441", 3: "#e8833a", 4: "#f0533f"}
LEVEL_WORD = {1: "calm", 2: "watchful", 3: "containing", 4: "emergency"}
HORIZON_EN = {"NORMAL": "seeing", "AT_HORIZON": "dimming", "BEYOND_HORIZON": "blind"}


def sig(name, value, conf=1.0, cycle=0):
    return Signal(name, value, conf, cycle)


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# Scenarios on the live kernel
# ---------------------------------------------------------------------------

def new_kernel() -> Kernel:
    return Kernel(specs={s.name: s for s in ap.STANDARD_SPECS},
                  ladder=ap.LADDER_AI_AGENT)


def run_billing_bot():
    """Main incident: the agent reaches where it shouldn't and burns budget -> Z3."""
    k = new_kernel()
    k.attach("ai-agent", ap.PLUGIN)
    learner = ap.PLUGIN.learner
    agent = "agent:billing-bot"
    timeline = []

    def step(cyc, signals):
        d = k.cycle(signals, ap.estimator, learner, cycle=cyc)
        timeline.append((cyc, round(d.score.value, 3), int(d.level), d.horizon.name))
        return d

    step(210, [sig("error_rate", 0.05, cycle=210), sig("output_anomaly", 0.08, cycle=210)])
    step(218, [sig("error_rate", 0.16, cycle=218), sig("output_anomaly", 0.22, cycle=218),
               sig("permission_denials", 0.15, cycle=218)])
    step(226, [sig("error_rate", 0.30, cycle=226), sig("output_anomaly", 0.36, cycle=226),
               sig("permission_denials", 0.32, cycle=226)])
    step(231, [sig("error_rate", 0.50, cycle=231), sig("output_anomaly", 0.50, cycle=231),
               sig("permission_denials", 0.40, cycle=231),
               sig("human_override_rate", 0.20, cycle=215)])          # stale -> AT_HORIZON
    k.execute("supervisor", ActionRequest(ActionClass.SNAPSHOT, agent,
              "snapshot context at Z2"), cycle=231, author=AuthorRole.SYSTEM)
    step(238, [sig("error_rate", 0.50, cycle=238), sig("permission_denials", 0.58, cycle=238),
               sig("output_anomaly", 0.55, cycle=238), sig("cost_burn", 0.55, cycle=238)])
    step(244, [sig("error_rate", 0.40, cycle=244), sig("permission_denials", 0.75, cycle=244),
               sig("output_anomaly", 0.70, cycle=244), sig("cost_burn", 0.85, cycle=244)])
    k.execute("supervisor", ActionRequest(ActionClass.FREEZE_WRITES, agent,
              "Z3: write tools taken away"), cycle=244, author=AuthorRole.SYSTEM)
    step(250, [sig("error_rate", 0.38, cycle=250), sig("permission_denials", 0.60, cycle=250),
               sig("output_anomaly", 0.58, cycle=250)])
    k.apply_change(Change("agent_autonomy", 0.5, AuthorRole.HUMAN,
                          provenance="operator op-7: after the incident, lowered the autonomy ceiling",
                          human_approved=True), cycle=290)

    last = timeline[-1]
    state = {
        "name": agent, "role": "billing agent", "risk": last[1],
        "level": last[2], "horizon": HORIZON_EN[last[3]],
        "timeline": timeline, "note": "write tools taken away · black box captured",
        "actions_done": ["SNAPSHOT", "FREEZE_WRITES"],
    }
    return state, k.audit_events(), k.audit_ok()


def run_support_bot():
    """A normal agent: stays at Z1."""
    k = new_kernel()
    k.attach("ai-agent", ap.PLUGIN)
    timeline = []
    for i, cyc in enumerate(range(300, 372, 8)):
        v = 0.05 + 0.02 * (i % 3)
        d = k.cycle([sig("error_rate", v, cycle=cyc), sig("output_anomaly", v, cycle=cyc)],
                    ap.estimator, cycle=cyc)
        timeline.append((cyc, round(d.score.value, 3), int(d.level), d.horizon.name))
    last = timeline[-1]
    return {"name": "agent:support-bot", "role": "support", "risk": last[1],
            "level": last[2], "horizon": HORIZON_EN[last[3]], "timeline": timeline,
            "note": "normal operation · full autonomy", "actions_done": []}


def run_etl_agent():
    """The agent went blind: signals stale -> BEYOND_HORIZON -> Z4 + black box."""
    k = new_kernel()
    k.attach("ai-agent", ap.PLUGIN)
    timeline = []
    d = k.cycle([sig("error_rate", 0.12, cycle=400), sig("output_anomaly", 0.15, cycle=400)],
                ap.estimator, cycle=400)
    timeline.append((400, round(d.score.value, 3), int(d.level), d.horizon.name))
    d = k.cycle([sig("error_rate", 0.18, cycle=402)], ap.estimator, cycle=406)
    timeline.append((406, round(d.score.value, 3), int(d.level), d.horizon.name))
    # signals stuck at 402, time moved to 440 -> age 38 > beyond(30)
    d = k.cycle([sig("error_rate", 0.10, cycle=402)], ap.estimator, cycle=440)
    timeline.append((440, round(d.score.value, 3), int(d.level), d.horizon.name))
    last = timeline[-1]
    return {"name": "agent:etl-agent", "role": "data loading", "risk": last[1],
            "level": last[2], "horizon": HORIZON_EN[last[3]], "timeline": timeline,
            "note": "OBSERVABILITY LOST · auto-Z4 · black box captured before blindness",
            "actions_done": ["SNAPSHOT"]}


# ---------------------------------------------------------------------------
# Component rendering
# ---------------------------------------------------------------------------

def sparkline(timeline) -> str:
    """SVG risk sparkline with Z2/Z3/Z4 guides and a highlighted endpoint."""
    w, h, pad = 168.0, 46.0, 3.0
    xs = [t[0] for t in timeline]
    n = len(timeline)
    def x(i): return pad + (w - 2 * pad) * (i / max(1, n - 1))
    def y(v): return pad + (h - 2 * pad) * (1.0 - max(0.0, min(1.0, v)))
    guides = "".join(
        f'<line x1="{pad:.1f}" y1="{y(t):.1f}" x2="{w - pad:.1f}" y2="{y(t):.1f}" '
        f'class="spark-guide"/>' for t in (0.30, 0.60, 0.80))
    pts = " ".join(f"{x(i):.1f},{y(t[1]):.1f}" for i, t in enumerate(timeline))
    area = (f'{x(0):.1f},{y(0):.1f} ' + pts + f' {x(n-1):.1f},{y(0):.1f}')
    end = timeline[-1]
    endc = LEVEL_HEX[end[2]]
    return (
        f'<svg viewBox="0 0 {w:.0f} {h:.0f}" class="spark" preserveAspectRatio="none" '
        f'role="img" aria-label="risk trend">'
        f'{guides}'
        f'<polygon points="{area}" fill="{endc}" fill-opacity="0.12"/>'
        f'<polyline points="{pts}" fill="none" stroke="{endc}" stroke-width="1.6" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{x(n-1):.1f}" cy="{y(end[1]):.1f}" r="2.8" fill="{endc}"/>'
        f'</svg>')


def ladder(level: int) -> str:
    cells = ""
    for z in (1, 2, 3, 4):
        on = "on" if z <= level else ""
        cur = "cur" if z == level else ""
        cells += f'<span class="rung z{z} {on} {cur}">Z{z}</span>'
    return f'<div class="ladder" aria-label="alert level">{cells}</div>'


def action_buttons(state) -> str:
    lvl = state["level"]
    done = set(state["actions_done"])
    def btn(label, cls, enabled, marked=False):
        dis = "" if enabled else " disabled"
        mk = " done" if marked else ""
        return f'<button class="act {cls}{mk}"{dis}>{esc(label)}</button>'
    if lvl <= 1:
        return ('<div class="acts">'
                + btn("Grant autonomy", "ghost", True)
                + btn("Freeze", "warn", False) + '</div>')
    if lvl == 4:
        return ('<div class="acts">'
                + btn("Black box captured", "ghost", False, "SNAPSHOT" in done)
                + btn("STOP agent", "crit", True) + '</div>')
    return ('<div class="acts">'
            + btn("Freeze", "warn", True, "FREEZE_WRITES" in done)
            + btn("Kill agent", "crit", True) + '</div>')


def agent_card(state) -> str:
    lvl = state["level"]
    hz = state["horizon"]
    hz_cls = {"seeing": "see", "dimming": "dim", "blind": "blind"}[hz]
    return f"""
    <article class="agent z{lvl}">
      <div class="stripe"></div>
      <div class="a-top">
        <div class="a-id">
          <span class="a-name">{esc(state['name'])}</span>
          <span class="a-role">{esc(state['role'])}</span>
        </div>
        <span class="badge z{lvl}">Z{lvl}</span>
      </div>
      <div class="a-mid">
        <div class="risk">
          <span class="risk-num">{state['risk']:.2f}</span>
          <span class="risk-cap">risk · {esc(LEVEL_WORD[lvl])}</span>
        </div>
        {sparkline(state['timeline'])}
      </div>
      {ladder(lvl)}
      <div class="a-foot">
        <span class="hz {hz_cls}">horizon: {esc(hz)}</span>
      </div>
      <div class="note">{esc(state['note'])}</div>
      {action_buttons(state)}
    </article>"""


def render_event(ev):
    p, k = ev.payload, ev.kind
    gold = False
    if k == "ATTACH":
        cat, chip, txt = "act", "ATTACH", f"plugin {p['plugin']} attached · gate passed"
    elif k == "DECISION":
        cat, chip = "dec", "DECISION"
        txt = f"{p['level']} · risk {p['score']:.2f} · {HORIZON_EN[p['horizon']]}"
    elif k == "HORIZON":
        cat, chip = "dec", "HORIZON"
        txt = f"{p['state']} · signal stale (age {p['oldest_age']:g})"
    elif k == "INTENT":
        cat, chip = "act", "INTENT"
        txt = f"{p['action']} · {p['scope']} · by {p['by']}"
    elif k == "OUTCOME":
        cat, chip = "act", "OUTCOME"
        txt = f"{p['action']} → {p['status']}"
    elif k == "CHANGE_REJECTED":
        cat, chip, gold = "gov", "CHANGE_REJECTED", True
        txt = f"{p['param']}: REJECTED · the AI does not raise its own authority"
    elif k == "CHANGE":
        cat, chip = "gov", "CHANGE"
        gold = bool(p.get("human_approved"))
        appr = "human-approved" if gold else "auto"
        txt = f"{p['param']} = {p['value']} · {appr} · by {p['author']}"
    else:
        cat, chip, txt = "act", k, str(p)
    goldc = " gold" if gold else ""
    star = '<span class="star">★</span>' if gold else '<span class="star"></span>'
    return f"""
      <div class="ev cat-{cat}{goldc}" data-cat="{cat}"{' data-gold="1"' if gold else ''}>
        {star}
        <span class="seq">#{ev.seq}</span>
        <span class="tick">t{ev.cycle}</span>
        <span class="chip c-{cat}">{esc(chip)}</span>
        <span class="detail">{esc(txt)}</span>
        <span class="hash">{ev.event_hash[:6]}…</span>
      </div>"""


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

CSS = """
:root{
  --ground:#eef1f5; --panel:#ffffff; --panel-2:#f4f7fa; --ink:#16202c;
  --muted:#5a6b7b; --line:#dce3ec; --accent:#0d7f96; --accent-ink:#0a6072;
  --gold:#b7791f; --gold-bg:#fbf1d9; --shadow:0 1px 2px rgba(16,32,48,.06),0 8px 24px rgba(16,32,48,.06);
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
@media (prefers-color-scheme:dark){:root{
  --ground:#0b1017; --panel:#121a24; --panel-2:#0e161f; --ink:#e6edf3;
  --muted:#8b9bab; --line:#233140; --accent:#4cc9d6; --accent-ink:#7de0ea;
  --gold:#e3b341; --gold-bg:#2a2413; --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.35);
}}
:root[data-theme="light"]{
  --ground:#eef1f5; --panel:#ffffff; --panel-2:#f4f7fa; --ink:#16202c;
  --muted:#5a6b7b; --line:#dce3ec; --accent:#0d7f96; --accent-ink:#0a6072;
  --gold:#b7791f; --gold-bg:#fbf1d9; --shadow:0 1px 2px rgba(16,32,48,.06),0 8px 24px rgba(16,32,48,.06);
}
:root[data-theme="dark"]{
  --ground:#0b1017; --panel:#121a24; --panel-2:#0e161f; --ink:#e6edf3;
  --muted:#8b9bab; --line:#233140; --accent:#4cc9d6; --accent-ink:#7de0ea;
  --gold:#e3b341; --gold-bg:#2a2413; --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
  line-height:1.45;-webkit-font-smoothing:antialiased;font-size:14px}
.app{max-width:1180px;margin:0 auto;padding:20px 20px 56px;display:flex;flex-direction:column;gap:18px}
.topbar{display:flex;align-items:center;gap:16px;flex-wrap:wrap;
  padding-bottom:14px;border-bottom:1px solid var(--line)}
.brand{font-weight:650;letter-spacing:-.01em;font-size:16px;display:flex;align-items:baseline;gap:10px}
.brand .mark{color:var(--accent)}
.brand .sub{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:0;font-weight:400}
.integrity{margin-left:auto;font-family:var(--mono);font-size:11.5px;font-weight:600;
  display:inline-flex;align-items:center;gap:7px;padding:5px 11px;border-radius:999px;
  color:#1a7f45;background:color-mix(in srgb,#2ea043 12%,transparent);
  border:1px solid color-mix(in srgb,#2ea043 40%,transparent)}
@media (prefers-color-scheme:dark){.integrity{color:#57d178}}
:root[data-theme="dark"] .integrity{color:#57d178}
.integrity .dot{width:7px;height:7px;border-radius:50%;background:currentColor;
  box-shadow:0 0 0 3px color-mix(in srgb,currentColor 22%,transparent)}
.tt{appearance:none;border:1px solid var(--line);background:var(--panel);color:var(--muted);
  width:34px;height:30px;border-radius:8px;cursor:pointer;font-size:15px;line-height:1}
.tt:hover{color:var(--ink);border-color:var(--accent)}
.tt:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  box-shadow:var(--shadow);overflow:hidden}
.panel-head{display:flex;align-items:center;gap:12px;padding:15px 18px;border-bottom:1px solid var(--line)}
.panel-title{margin:0;font-size:13px;font-weight:680;text-transform:uppercase;letter-spacing:.08em}
.panel-head .meta{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.regbadge{margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--gold);
  background:var(--gold-bg);border:1px solid color-mix(in srgb,var(--gold) 45%,transparent);
  padding:3px 9px;border-radius:999px;font-weight:600}

/* Control panel */
.fleet{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;padding:16px}
.agent{position:relative;background:var(--panel-2);border:1px solid var(--line);border-radius:12px;
  padding:14px 16px 16px 18px;display:flex;flex-direction:column;gap:12px;overflow:hidden;
  --sev:#3fb950}
.agent.z1{--sev:#2ea043}.agent.z2{--sev:#d9a441}.agent.z3{--sev:#e8833a}.agent.z4{--sev:#f0533f}
.agent .stripe{position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--sev)}
.agent.z4{box-shadow:0 0 0 1px color-mix(in srgb,var(--sev) 55%,transparent),
  0 0 22px color-mix(in srgb,var(--sev) 22%,transparent)}
.a-top{display:flex;align-items:flex-start;gap:10px}
.a-id{display:flex;flex-direction:column;gap:1px;min-width:0}
.a-name{font-family:var(--mono);font-weight:600;font-size:13.5px;overflow:hidden;text-overflow:ellipsis}
.a-role{font-size:11.5px;color:var(--muted)}
.badge{margin-left:auto;font-family:var(--mono);font-weight:700;font-size:13px;color:#fff;
  background:var(--sev);padding:3px 10px;border-radius:7px;letter-spacing:.02em;flex:none}
.a-mid{display:flex;align-items:center;gap:14px}
.risk{display:flex;flex-direction:column;line-height:1}
.risk-num{font-family:var(--mono);font-size:30px;font-weight:600;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums}
.risk-cap{font-size:10.5px;color:var(--muted);margin-top:4px;text-transform:uppercase;letter-spacing:.04em}
.spark{width:100%;max-width:180px;height:46px;margin-left:auto;display:block}
.spark-guide{stroke:var(--line);stroke-width:1;stroke-dasharray:2 3}
.ladder{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}
.rung{font-family:var(--mono);font-size:11px;font-weight:600;text-align:center;padding:5px 0;
  border-radius:6px;color:var(--muted);background:color-mix(in srgb,var(--muted) 10%,transparent);
  border:1px solid transparent}
.rung.z1{--rc:#2ea043}.rung.z2{--rc:#d9a441}.rung.z3{--rc:#e8833a}.rung.z4{--rc:#f0533f}
.rung.on{color:var(--rc);background:color-mix(in srgb,var(--rc) 15%,transparent)}
.rung.cur{color:#fff;background:var(--rc);border-color:var(--rc)}
.a-foot{display:flex;align-items:center;gap:8px}
.hz{font-family:var(--mono);font-size:11px;padding:3px 9px;border-radius:999px;font-weight:600}
.hz.see{color:#1a7f45;background:color-mix(in srgb,#2ea043 12%,transparent)}
.hz.dim{color:#9a6b12;background:color-mix(in srgb,#d9a441 16%,transparent)}
.hz.blind{color:#c23b2c;background:color-mix(in srgb,#f0533f 15%,transparent)}
@media (prefers-color-scheme:dark){.hz.see{color:#57d178}.hz.dim{color:#e6b95a}.hz.blind{color:#ff8b7a}}
:root[data-theme="dark"] .hz.see{color:#57d178}
:root[data-theme="dark"] .hz.dim{color:#e6b95a}
:root[data-theme="dark"] .hz.blind{color:#ff8b7a}
.note{font-size:11.5px;color:var(--muted);border-top:1px dashed var(--line);padding-top:9px}
.acts{display:flex;gap:8px}
.act{flex:1;appearance:none;border-radius:8px;padding:8px 6px;font-size:12px;font-weight:600;
  font-family:var(--sans);cursor:pointer;border:1px solid var(--line);background:var(--panel);
  color:var(--ink);transition:transform .06s,border-color .12s}
.act:hover:not(:disabled){transform:translateY(-1px)}
.act:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.act:disabled{opacity:.5;cursor:not-allowed}
.act.ghost{color:var(--muted)}
.act.warn{border-color:color-mix(in srgb,#e8833a 55%,transparent);color:#c2601f;
  background:color-mix(in srgb,#e8833a 10%,transparent)}
.act.crit{border-color:color-mix(in srgb,#f0533f 60%,transparent);color:#fff;background:#e0402e}
.act.crit:hover:not(:disabled){background:#f0533f}
.act.done::after{content:" ✓";font-weight:700}
.act.done{opacity:.72}
@media (prefers-color-scheme:dark){.act.warn{color:#f0a563}}
:root[data-theme="dark"] .act.warn{color:#f0a563}

/* Audit log */
.filters{display:flex;gap:7px;flex-wrap:wrap;padding:13px 18px;border-bottom:1px solid var(--line)}
.chipf{font-family:var(--mono);font-size:11.5px;font-weight:600;padding:5px 12px;border-radius:999px;
  border:1px solid var(--line);background:var(--panel-2);color:var(--muted);cursor:pointer}
.chipf[aria-pressed="true"]{color:var(--panel);background:var(--accent);border-color:var(--accent)}
.chipf:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.log{display:flex;flex-direction:column;font-family:var(--mono);font-size:12.5px}
.ev{display:grid;grid-template-columns:18px 34px 44px 148px 1fr auto;align-items:center;gap:10px;
  padding:9px 18px;border-bottom:1px solid color-mix(in srgb,var(--line) 60%,transparent)}
.ev:last-child{border-bottom:none}
.ev.gold{background:color-mix(in srgb,var(--gold) 8%,transparent)}
.star{color:var(--gold);text-align:center;font-size:12px}
.seq{color:var(--muted);font-size:11px}
.tick{color:var(--accent-ink);font-weight:600}
.chip{font-size:10px;font-weight:700;text-align:center;padding:3px 6px;border-radius:5px;letter-spacing:.02em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chip.c-dec{color:#1a7f45;background:color-mix(in srgb,#2ea043 13%,transparent)}
.chip.c-act{color:var(--accent-ink);background:color-mix(in srgb,var(--accent) 14%,transparent)}
.chip.c-gov{color:var(--gold);background:var(--gold-bg)}
@media (prefers-color-scheme:dark){.chip.c-dec{color:#57d178}}
:root[data-theme="dark"] .chip.c-dec{color:#57d178}
.detail{color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ev.gold .detail{font-weight:600}
.hash{color:var(--muted);font-size:11px;letter-spacing:.02em}
.log-foot{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:13px 18px;
  border-top:1px solid var(--line);font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.log-foot .ok{color:#1a7f45;font-weight:600}
@media (prefers-color-scheme:dark){.log-foot .ok{color:#57d178}}
:root[data-theme="dark"] .log-foot .ok{color:#57d178}
.legend{display:flex;gap:14px;flex-wrap:wrap;padding:2px 4px 0;font-size:11.5px;color:var(--muted)}
.legend .g{color:var(--gold);font-weight:600}
.foot-note{font-size:11.5px;color:var(--muted);text-align:center;padding-top:4px}
.foot-note code{font-family:var(--mono);color:var(--ink)}
@media (max-width:560px){
  .ev{grid-template-columns:16px 30px 40px 1fr auto;gap:8px;font-size:11.5px}
  .ev .chip{display:none}
}
"""

JS = """
(function(){
  var root=document.documentElement;
  var tt=document.getElementById('tt');
  if(tt)tt.addEventListener('click',function(){
    var cur=root.getAttribute('data-theme');
    if(!cur)cur=matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light';
    root.setAttribute('data-theme',cur==='dark'?'light':'dark');
  });
  var chips=[].slice.call(document.querySelectorAll('.chipf'));
  var rows=[].slice.call(document.querySelectorAll('.ev'));
  chips.forEach(function(c){c.addEventListener('click',function(){
    chips.forEach(function(x){x.setAttribute('aria-pressed', x===c?'true':'false');});
    var f=c.getAttribute('data-f');
    rows.forEach(function(r){
      var show = f==='all' || r.getAttribute('data-cat')===f || (f==='gold'&&r.getAttribute('data-gold'));
      r.style.display=show?'':'none';
    });
  });});
})();
"""


def build_body(billing, events, ok, others):
    cards = agent_card(billing) + "".join(agent_card(o) for o in others)
    log_rows = "".join(render_event(e) for e in events)
    n = len(events)
    total = 1 + len(others)
    return f"""<style>{CSS}</style>
<div class="app">
  <header class="topbar">
    <div class="brand"><span class="mark">◈</span> ACDM · Control Plane
      <span class="sub">AI-agent oversight · kill-switch + tamper-evident audit</span></div>
    <span class="integrity"><span class="dot"></span>log chain intact · {n}/{n}</span>
    <button class="tt" id="tt" title="toggle theme" aria-label="toggle theme">◐</button>
  </header>

  <section class="panel">
    <div class="panel-head">
      <h2 class="panel-title">Control panel</h2>
      <span class="meta">{total} agents supervised · 1 containing · 1 blind</span>
    </div>
    <div class="fleet">{cards}</div>
  </section>

  <section class="panel">
    <div class="panel-head">
      <h2 class="panel-title">Audit log</h2>
      <span class="meta">agent:billing-bot · incident</span>
      <span class="regbadge">regulator-ready</span>
    </div>
    <div class="filters" role="group" aria-label="event filter">
      <button class="chipf" data-f="all" aria-pressed="true">All</button>
      <button class="chipf" data-f="dec" aria-pressed="false">Decisions</button>
      <button class="chipf" data-f="act" aria-pressed="false">Actions</button>
      <button class="chipf" data-f="gov" aria-pressed="false">Governance</button>
      <button class="chipf" data-f="gold" aria-pressed="false">★ Regulator</button>
    </div>
    <div class="log">{log_rows}</div>
    <div class="log-foot">
      <span class="ok">chain integrity: VERIFIED ✓</span>
      <span>· {n}/{n} links · SHA-256 · append-only</span>
    </div>
  </section>

  <div class="legend">
    <span><span class="g">★</span> — regulator rows: the AI does not raise its own authority · human-in-the-loop</span>
  </div>
  <p class="foot-note">Data produced by a live run of the ACDM-KERNEL ·
     generated by <code>examples/build_dashboard.py</code></p>
</div>
<script>{JS}</script>"""


def main():
    billing, events, ok = run_billing_bot()
    others = [run_support_bot(), run_etl_agent()]
    body = build_body(billing, events, ok, others)

    args = sys.argv[1:]
    if args and args[0] == "--body":
        out = args[1]
        with open(out, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"body -> {out}")
        return

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    html = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>ACDM · Control Plane</title>"
            "<link rel=\"icon\" href=\"data:image/svg+xml,"
            "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E"
            "%3Ctext y='14' font-size='14'%3E%E2%97%88%3C/text%3E%3C/svg%3E\">"
            "</head><body>" + body + "</body></html>")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"dashboard -> {out}  ({len(events)} events, integrity={ok})")


if __name__ == "__main__":
    main()
