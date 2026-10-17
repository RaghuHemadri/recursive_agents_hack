"""Groundhog for Azure: text-guided, multimodal computer use of the Azure portal on the Groundhog harness.

    GROUNDHOG_WORLD=azure python -m groundhog.app        ->  http://localhost:7860

Type a task; while it runs, anything you type steers the team; agents ask you when stuck, off track, or before a risky
click (approve/deny icons). After each task you give thumbs up/down. That verdict:
  - is recorded as a validation (worked/failed) and turned into Azure lessons in the shared brain (reflector + curator);
  - updates the adaptive router's measured success per job and model (which models fill Switchyard's tiers);
  - gates harness adaptation: after a failure, human steering, or a long success, the improver proposes ONE edit
    (prompt, sub-agent prompt, skill or routing) -> it runs as a trial on the next task -> thumbs up promotes it to
    harness v{N+1} (git commit), thumbs down rolls it back. The unattended evolve loop (same improver, validation gate)
    still runs with `python -m groundhog.evolve evolve`.
"""
import difflib
import json
import os
import re
import shutil
import sqlite3
import threading
import time

os.environ.setdefault("GROUNDHOG_WORLD", "azure")

from flask import Flask, jsonify, request, send_file  # noqa: E402

from .agent import Team, load_harness  # noqa: E402
from .brain import Brain  # noqa: E402
from .config import HARNESS, ROOT, RUNS, world  # noqa: E402
from .evolve import _log, current_version, kv_get, kv_set, learn, materialise, propose, remember_validations  # noqa: E402
from .human import Human  # noqa: E402
from .llm import LLM  # noqa: E402
from .router import BENCH, Router  # noqa: E402

W, app, human, llm = world(), Flask(__name__), Human(), LLM()
brain = Brain(RUNS / "brain.db")
state = {"busy": False, "task": None, "adapting": False}
ACTIONS = {"click", "type_text", "press_key", "go_to_url", "scroll", "observe", "read_page", "wait"}


def harness_dir() -> tuple:
    """(dir, label): the trial candidate if one is active, else the latest accepted version."""
    trial = kv_get("trial")
    if trial and pathlib_exists(trial["dir"]):
        return trial["dir"], f"v{trial['base']}+trial"
    v = current_version()
    return str(HARNESS / f"v{v}"), f"v{v}"


def pathlib_exists(p) -> bool:
    return os.path.isdir(p)


def judge_trial(res: dict, label: str) -> None:
    trial = kv_get("trial")
    if not trial or not label.endswith("+trial"):
        return
    e, n = trial["edit"], trial["base"]
    if res["passed"]:
        dest = HARNESS / f"v{n + 1}"
        shutil.copytree(trial["dir"], dest, dirs_exist_ok=True)
        os.system(f'cd "{ROOT}" && git add "{HARNESS.name}" >/dev/null 2>&1 && git commit -qm "harness {HARNESS.name}/v{n + 1}: '
                  f'{re.sub(r"[^A-Za-z0-9 ,.:;()+-]", "", e["hypothesis"])[:150]}" >/dev/null 2>&1')
        status, msg = "accepted", f"Trial edit to {e['file']} PROMOTED to harness v{n + 1} (thumbs up on the trial task)."
    else:
        status, msg = "rejected", f"Trial edit to {e['file']} ROLLED BACK (thumbs down on the trial task)."
    _log("archive.jsonl", dict(ts=time.time(), gen=f"live-{trial['ts']}", version=n, status=status, files=[e["file"]],
                               hypotheses=[e["hypothesis"]], reason="human verdict on the trial task", diff=trial["diff"]))
    brain.record_validation("gate", e["file"], e["hypothesis"], res["passed"], f"live trial: {status}", res["task_id"], "live")
    kv_set("trial", None)
    human.emit({"role": "system", "kind": "harness", "text": msg})


def adapt_harness(res: dict, steered: list, verdict: str) -> None:
    """Improver proposes one edit from this task; it becomes the trial for the next task."""
    if kv_get("trial"):
        return
    n_actions = sum(1 for e in res["trace"] if e.get("tool") in ACTIONS)
    if res["passed"] and not steered and n_actions < 8:
        return
    why = ("the human gave thumbs DOWN" + (f" ({verdict})" if verdict.lower() not in ("no", "") else "")) if not res["passed"] \
        else "the human had to steer: " + " | ".join(steered) if steered else f"it SUCCEEDED but needed {n_actions} screen actions"
    note = (f"LIVE TASK: {res.get('question', '')!r}. This task is the trace above; {why}. "
            + ("Write or update ONE skill (skills/<name>/SKILL.md, Deep Agents frontmatter) that captures the procedure that "
               "worked, step by step, so next time it needs fewer steps. " if res["passed"] else
               "Make ONE edit (prompt, sub-agent prompt, skill or routing) that fixes what went wrong or encodes the human's "
               "guidance for all future tasks. ") + "Keep it general (no task ids).")
    base = HARNESS / f"v{current_version()}"
    state["adapting"] = True
    human.emit({"role": "system", "kind": "harness", "text": f"Improver ({why}) is proposing a harness edit..."})
    try:
        edits = propose(llm, base, {"summary": {"tag": "live"}, "results": [res]}, n_edits=1, note=note)
    finally:
        state["adapting"] = False
    if not edits:
        human.emit({"role": "system", "kind": "harness", "text": "Improver proposed no edit that passed the filters."})
        return
    e, ts = edits[0], int(time.time())
    d = materialise(base, [e], RUNS / "candidates" / f"live-{ts}")
    old = (base / e["file"]).read_text().splitlines() if (base / e["file"]).exists() else []
    diff = "\n".join(difflib.unified_diff(old, (d / e["file"]).read_text().splitlines(), f"{base.name}/{e['file']}",
                                          f"trial/{e['file']}", lineterm=""))
    kv_set("trial", dict(dir=str(d), base=current_version(), edit={k: e[k] for k in ("file", "hypothesis")}, diff=diff, ts=ts))
    _log("archive.jsonl", dict(ts=time.time(), gen=f"live-{ts}", version=current_version(), status="trial",
                               files=[e["file"]], hypotheses=[e["hypothesis"]], diff=diff))
    human.emit({"role": "system", "kind": "harness", "text": f"Trial edit to {e['file']}: {e['hypothesis']}\n"
                                                              "It runs on the next task; your thumbs up/down decides."})


def run_task(text: str) -> None:
    state.update(busy=True, task=dict(text=text, t0=time.time(), n0=len(human.events)))
    try:
        hdir, label = harness_dir()
        h = load_harness(__import__("pathlib").Path(hdir))
        task = dict(id=f"live-{int(time.time())}", template="live", question=text)
        human.emit({"role": "system", "kind": "task", "text": f"Task started on harness {label}: {text}"})
        used = brain.ids_in_render()
        res = Team(h, brain.render(), llm, trace_dir=RUNS / "traces" / "live", human=human, step_cap=24,
                   task_timeout_s=1500).run(task)
        res.update(brain_used=used, question=text)
        n_actions = sum(1 for e in res["trace"] if e.get("tool") in ACTIONS)
        verdict = human.ask(f"Finished with answer {res['answer']!r} after {n_actions} screen actions "
                            f"(now at {(res['final'] or {}).get('title', '')}). Did it do the task correctly?", kind="verdict") or ""
        res["passed"] = verdict.strip().lower().startswith(("y", "\U0001f44d"))
        steered = [e["text"] for e in res["trace"] if e.get("kind") in ("steer", "human")]  # steering + answers to its questions
        res["trace"].append(dict(step=len(res["trace"]), role="human", kind="verdict", text=verdict))
        (RUNS / "traces" / "live" / f"{task['id']}.json").write_text(json.dumps(res, default=str, indent=1))
        for role, models in res.get("used", {}).items():  # router learns from this interaction
            for m in models:
                Router.record(res["jobs"][role], m, res["passed"])
        learn(brain, llm, Router(h["routing"]), [res])
        remember_validations(brain, [res], "live")
        human.emit({"role": "system", "kind": "learned", "text": "Brain: " + ("; ".join(res.get("brain_events", [])) or "no change")[:600]
                    + f"\nRouter: recorded {'success' if res['passed'] else 'failure'} for "
                    + ", ".join(f"{r}={m.split('/')[-1]}" for r, ms in res.get("used", {}).items() for m in ms)})
        judge_trial(res, label)
        adapt_harness(res, steered, verdict)
    except Exception as e:  # keep the session alive; the error is shown in the chat
        human.emit({"role": "system", "kind": "error", "text": f"{type(e).__name__}: {e}"[:500]})
    finally:
        state["busy"] = False


@app.post("/send")
def send():
    text = (request.json or {}).get("text", "").strip()
    if not text:
        return jsonify(ok=False)
    if human.waiting or state["busy"]:
        return jsonify(ok=True, kind=human.say(text))
    human.emit({"role": "human", "kind": "task", "text": text})
    threading.Thread(target=run_task, args=(text,), daemon=True, name="task").start()
    return jsonify(ok=True, kind="task")


@app.post("/open")
def open_portal():
    W.BROWSER.run(lambda: (W.BROWSER.page.goto(W.HOME, timeout=60_000), W.BROWSER.observe()))
    human.emit({"role": "system", "kind": "info", "text": "Opened the Azure portal in the browser window. Sign in there if asked."})
    return jsonify(ok=True)


@app.get("/observe")
def observe_now():
    o = W.BROWSER.run(W.BROWSER.observe)
    return jsonify(url=o["url"], title=o["title"], elements=o["elements"][:3000])


@app.get("/events")
def events():
    after = int(request.args.get("after", -1))
    t = state["task"]
    steps = sum(1 for e in human.events[t["n0"]:] if e.get("tool") in ACTIONS) if t else 0
    return jsonify(events=[e for e in human.events if e["n"] > after], busy=state["busy"], waiting=human.waiting,
                   waiting_kind=human.waiting_kind, steps=steps, adapting=state["adapting"])


@app.get("/screen.jpg")
def screen():
    p = RUNS / "screens" / "latest.jpg"
    return send_file(p, mimetype="image/jpeg", max_age=0) if p.exists() else ("", 204)


def _jl(name):
    p = RUNS / name
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


@app.get("/api/harness")
def api_harness():
    hdir, label = harness_dir()
    import pathlib
    h = pathlib.Path(hdir)
    files = {str(p.relative_to(h)): p.read_text() for p in sorted(h.rglob("*")) if p.is_file()}
    versions = sorted(int(p.name[1:]) for p in HARNESS.glob("v*") if p.name[1:].isdigit())
    return jsonify(active=label, versions=versions, trial=kv_get("trial"), files=files,
                   archive=[a for a in _jl("archive.jsonl") if a.get("status") in ("trial", "accepted", "rejected", "candidate")][-30:])


@app.get("/api/routing")
def api_routing():
    hdir, _ = harness_dir()
    import pathlib
    cfg = load_harness(pathlib.Path(hdir))["routing"]
    r = Router(cfg)
    jobs = {"lead": r.job_for("lead", None), "scout": r.job_for("scout", None), "reflector": r.job_for("reflector", None),
            "improver": r.job_for("improver", None)}
    tables = {}
    for job in [*cfg["jobs"], *cfg["task_jobs"]]:
        vision = job in cfg["task_jobs"] or job == jobs["scout"]
        tables[job] = [dict(model=m, utility=round(u, 3), bench=p, post=post, n=n) for u, m, p, post, n in r.table(job, vision=vision)]
    tiers = {f"{role}:{job or ''}": r.tiers(role, job) for role, job in
             (("lead", None), ("scout", None), *(("analyst", j) for j in cfg["task_jobs"]))}
    log = _jl("routing.jsonl")
    sy = [x for x in log if x.get("event") == "switchyard"][-60:]
    mix = {}
    for x in log:
        if "role" in x and "event" not in x and "error" not in x:
            k = (x["role"], x["model"])
            mix.setdefault(k, [0, 0.0])
            mix[k][0] += 1
            mix[k][1] += x.get("cost", 0)
    return jsonify(tables=tables, tiers=tiers, switchyard=sy, sy_counts={t: sum(1 for x in sy if x["tier"] == t) for t in ("capable", "efficient")},
                   events=[x for x in log if x.get("event") in ("escalate", "fallback")][-20:],
                   mix=[dict(role=k[0], model=k[1], calls=v[0], cost=round(v[1], 4)) for k, v in sorted(mix.items())],
                   benchmarks={m: dict(scores=v["scores"], vision=bool(v.get("vision"))) for m, v in BENCH.items() if v["family"] == "nemotron"},
                   spent=round(sum(x.get("cost", 0) for x in log), 4))


@app.get("/api/brain")
def api_brain():
    bs = brain.bullets(include_hidden=True)
    with sqlite3.connect(RUNS / "brain.db") as db:
        log = [dict(ts=t, event=e) for t, e in db.execute("SELECT ts, event FROM log ORDER BY ts DESC LIMIT 40")]
    return jsonify(bullets=bs, validations=brain.validations()[-40:], log=log, md=brain.render())


@app.get("/")
def index():
    return PAGE


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Groundhog · Azure computer use</title><style>
:root{--b:#e3e6ea;--n:#76b900}*{box-sizing:border-box}body{font-family:system-ui,sans-serif;margin:0;color:#1d2330;background:#f7f8fa}
header{display:flex;align-items:center;gap:14px;padding:8px 14px;background:#111;color:#fff}header b{color:var(--n)}
nav button{background:none;border:0;color:#bbb;padding:6px 10px;cursor:pointer;font-size:14px}nav button.on{color:#fff;border-bottom:2px solid var(--n)}
.tab{display:none;height:calc(100vh - 46px)}.tab.on{display:flex}#live{gap:0}#l{width:42%;display:flex;flex-direction:column;border-right:1px solid var(--b);background:#fff}
#log{flex:1;overflow:auto;padding:8px;font-size:12.5px}.e{margin:3px 0;padding:5px 7px;border-radius:6px;white-space:pre-wrap;word-break:break-word}
.human{background:#e8f0fe}.question{background:#fff3cd;font-weight:600}.error{background:#f8d7da}.system,.learned,.harness{background:#eaf6e3}
.harness{border-left:4px solid var(--n)}.route{background:#f1ecff;font-size:11.5px}.tool,.task,.final,.think,.steer,.escalate,.fallback,.reroute,.guard{background:#f4f5f7}
#bar{padding:6px 10px;font-size:12px;color:#444;border-bottom:1px solid var(--b);display:flex;gap:10px;align-items:center}
#steps{font-weight:700;font-size:14px;color:#111;background:#eef;padding:2px 8px;border-radius:10px}
form{display:flex;padding:8px;gap:6px;border-top:1px solid var(--b)}input{flex:1;padding:8px;border:1px solid #ccc;border-radius:6px}
#fb{display:none;padding:8px;gap:10px;border-top:1px solid var(--b);align-items:center;background:#fffbe8}#fb.on{display:flex}
.icon{font-size:26px;cursor:pointer;border:1px solid #ddd;background:#fff;border-radius:10px;padding:4px 12px}.icon:hover{background:#f0f0f0}
#r{flex:1;padding:10px;overflow:auto}#r img{max-width:100%;border:1px solid #ccc;border-radius:4px}
.quick button{font-size:12px;margin:0 4px 4px 0}.pane{flex:1;overflow:auto;padding:14px;display:block}
h3{margin:14px 0 6px}table{border-collapse:collapse;font-size:12.5px;background:#fff}td,th{border:1px solid var(--b);padding:4px 7px;text-align:left;vertical-align:top}
pre{background:#fff;border:1px solid var(--b);padding:8px;font-size:12px;white-space:pre-wrap;max-height:340px;overflow:auto}
.pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;margin-right:4px}.ok{background:#dff3d0}.bad{background:#f8d7da}.tr{background:#fff3cd}.q{background:#f8d7da;color:#900}
.arch{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;max-width:1150px}.box{background:#fff;border:1px solid var(--b);border-radius:8px;padding:9px;font-size:12.5px}
.box b{display:block;margin-bottom:3px}.nv{color:#4a7a00;font-weight:600}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.diff .a{color:#11690c}.diff .d{color:#a00}</style></head><body>
<header><b>Groundhog</b><span>Azure computer use · Nemotron · NeMo Switchyard</span><nav>
<button class="on" data-t="live">Live</button><button data-t="arch">Architecture</button><button data-t="harness">Harness</button>
<button data-t="routing">Routing</button><button data-t="brain">Brain</button></nav><span id="spent" style="margin-left:auto;font-size:12px"></span></header>

<div class="tab on" id="live"><div id="l"><div id="bar"><span id="steps">0 steps</span><span id="st"></span></div><div id="log"></div>
<div id="fb"><span id="fbq"></span><button class="icon" id="up" title="Worked">&#128077;</button><button class="icon" id="down" title="Did not work">&#128078;</button>
<input id="why" placeholder="What went wrong? (optional)" style="display:none"><button id="whysend" style="display:none">Send</button></div>
<div id="ap" style="display:none;padding:8px;gap:10px;border-top:1px solid #ddd;background:#fff3cd;align-items:center"><span id="apq"></span>
<button class="icon" id="yes" title="Approve">&#9989;</button><button class="icon" id="no" title="Deny">&#10060;</button></div>
<form id="f"><input id="t" placeholder="Give a task, steer while it runs, or answer a question" autocomplete="off"><button>Send</button></form>
<div class="quick" style="padding:0 8px 8px"><button onclick="fetch('/open',{method:'POST'})">Open Azure portal (sign in)</button>
<button onclick="go('Open the list of virtual machines.')">Short task</button>
<button onclick="go('Open the form to create a new resource group. Name it groundhog-demo-rg, select the region East US, go to the Tags tab and add a tag with name env and value demo, then open Review + create and read the summary back to me. Do not click Create.')">Long task (10+ steps)</button>
<button onclick="go('Go to Subscriptions and open my subscription. Tell me its subscription ID and status. Then open its Access control (IAM), go to Role assignments and tell me how many role assignments there are. Finally open Cost Management cost analysis for it and tell me the cost so far this month.')">Long read task</button></div></div>
<div id="r"><img id="s" src="/screen.jpg"></div></div>

<div class="tab pane" id="arch"><h3>Architecture (live)</h3><div class="arch" id="archbox"></div>
<p style="font-size:12.5px;max-width:1100px">Every prompt goes through <b>NeMo Switchyard</b> (stage router): errors, spinning or exploration send it to the capable tier, clean progress to the efficient tier. The <b>adaptive benchmark router</b> fills the tiers per job from published Nemotron benchmarks plus measured success (updated by every thumbs up/down). Sub-agents see the screenshot with numbered marks and act by number. <b>BRAIN.md</b> (curated lessons) is in every prompt; the <b>improver</b> edits prompts, sub-agents, skills and routing as trials that your verdict promotes or rolls back.</p></div>

<div class="tab pane" id="harness"><div class="grid2"><div><h3>Harness versions</h3><div id="hv"></div><h3>Edit history (trials, promotions, rollbacks)</h3><div id="ha"></div></div>
<div><h3>Current files (<span id="hact"></span>)</h3><div id="hf"></div></div></div></div>

<div class="tab pane" id="routing"><div class="grid2"><div><h3>NeMo Switchyard decisions (latest first)</h3><div id="syc"></div><div id="sy"></div></div>
<div><h3>Tier pairs per role/job (from the adaptive router)</h3><div id="tiers"></div><h3>Model mix</h3><div id="mix"></div><h3>Escalations / fallbacks</h3><div id="esc"></div></div></div>
<h3>Adaptive router: benchmark prior vs measured posterior</h3><div id="tables" class="grid2"></div></div>

<div class="tab pane" id="brain"><div class="grid2"><div><h3>Lessons <span id="bc"></span></h3><svg id="growth" width="520" height="90"></svg><div id="bl"></div></div>
<div><h3>Validations (worked / failed)</h3><div id="bv"></div><h3>Curator log</h3><div id="blog"></div></div></div></div>

<script>
const $=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])),short=m=>String(m||'').split('/').pop();
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('nav button,.tab').forEach(x=>x.classList.remove('on'));b.classList.add('on');$('#'+b.dataset.t).classList.add('on');refresh()});
let after=-1;const log=$('#log');
async function send(t){await fetch('/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})})}
function go(t){$('#t').value=t;$('#f').requestSubmit()}
function cls(e){return e.kind==='question'?'question':e.role==='human'?'human':e.role==='system'?(e.kind==='error'?'error':e.kind):(e.kind||'')}
function txt(e){let who=e.role+(e.model?' ('+short(e.model)+')':'');let body=e.text||'';if(e.tool)body=e.tool+' '+JSON.stringify(e.args||{})+(e.result?'\n-> '+e.result:'');return who+' · '+(e.kind||'')+'\n'+body}
async function poll(){try{const d=await (await fetch('/events?after='+after)).json();
 for(const e of d.events){after=e.n;const div=document.createElement('div');div.className='e '+cls(e);div.textContent=txt(e);log.appendChild(div);log.scrollTop=1e9}
 $('#steps').textContent=d.steps+' steps';$('#st').textContent=d.waiting?'Waiting for you':(d.adapting?'Improver is adapting the harness...':(d.busy?'Working... (type to steer)':'Idle: type a task'));
 $('#fb').classList.toggle('on',d.waiting_kind==='verdict');if(d.waiting_kind==='verdict')$('#fbq').textContent=d.waiting;
 $('#ap').style.display=d.waiting_kind==='approval'?'flex':'none';if(d.waiting_kind==='approval')$('#apq').textContent=d.waiting;
 $('#s').src='/screen.jpg?t='+Date.now()}catch(e){} setTimeout(poll,1500)}
$('#up').onclick=()=>send('yes');$('#down').onclick=()=>{$('#why').style.display='';$('#whysend').style.display='';$('#why').focus()};
$('#whysend').onclick=()=>{send('no'+($('#why').value?': '+$('#why').value:''));$('#why').value='';$('#why').style.display='none';$('#whysend').style.display='none'};
$('#yes').onclick=()=>send('yes');$('#no').onclick=()=>send('no');
$('#f').onsubmit=async ev=>{ev.preventDefault();const t=$('#t');if(!t.value)return;await send(t.value);t.value=''};
function tbl(rows,cols){return '<table><tr>'+cols.map(c=>'<th>'+c[0]+'</th>').join('')+'</tr>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+c[1](r)+'</td>').join('')+'</tr>').join('')+'</table>'}
function diff(d){return '<pre class="diff">'+esc(d).split('\n').map(l=>l.startsWith('+')?'<span class="a">'+l+'</span>':l.startsWith('-')?'<span class="d">'+l+'</span>':l).join('\n')+'</pre>'}
async function refresh(){const tab=document.querySelector('nav button.on').dataset.t;try{
 const R=await (await fetch('/api/routing')).json();$('#spent').textContent='$'+R.spent+' spent';
 if(tab==='arch'){const t=R.tiers,tp=k=>t[k]?'<span class="nv">'+short(t[k][0])+'</span> / '+short(t[k][1]):'';
  $('#archbox').innerHTML=['<b>You (text)</b>task · steer anytime · answer questions · approve risky clicks · 👍/👎',
  '<b>Lead</b>plans + definition of done, delegates via Deep Agents <i>task</i><br>Switchyard tiers: '+tp('lead:'),
  '<b>Scout</b>explores the portal (screenshot)<br>tiers: '+tp('scout:'),
  '<b>Analyst-&lt;job&gt;</b>acts: click / type / scroll by mark number<br>web_navigation: '+tp('analyst:web_navigation')+'<br>form_filling: '+tp('analyst:form_filling'),
  '<b>Verifier-&lt;job&gt;</b>independent check of the screen, different model than the analyst',
  '<b>Browser</b>Playwright Chromium, Set-of-Marks screenshots, frozen safety policy',
  '<b>NeMo Switchyard</b>stage router per prompt: '+R.sy_counts.capable+' capable / '+R.sy_counts.efficient+' efficient',
  '<b>Adaptive router</b>Nemotron benchmarks (WebVoyager, OSWorld, OCRBench, τ-bench, IFBench) + measured success',
  '<b>Brain</b>BRAIN.md in every prompt; reflector → curator (dedup, quarantine, trust)',
  '<b>Improver</b>proposes one edit (prompt / sub-agent / skill / routing) → trial → your verdict promotes or rolls back',
  '<b>Evolve loop</b>unattended: train → 3 edits → validation gate → harness v{N+1}',
  '<b>Models</b>'+Object.keys(R.benchmarks).map(short).join('<br>')].map(x=>'<div class="box">'+x+'</div>').join('')}
 if(tab==='routing'){$('#syc').innerHTML='<span class="pill ok">efficient '+R.sy_counts.efficient+'</span><span class="pill tr">capable '+R.sy_counts.capable+'</span>';
  $('#sy').innerHTML=tbl(R.switchyard.slice().reverse().slice(0,30),[['role',r=>r.role],['job',r=>r.job],['tier',r=>'<span class="pill '+(r.tier==='capable'?'tr':'ok')+'">'+r.tier+'</span>'],['model',r=>short(r.model)],['signal',r=>esc(JSON.stringify(r.evidence))]]);
  $('#tiers').innerHTML=tbl(Object.entries(R.tiers),[['role:job',r=>r[0]],['capable',r=>short(r[1][0])],['efficient',r=>short(r[1][1])]]);
  $('#mix').innerHTML=tbl(R.mix,[['role',r=>r.role],['model',r=>short(r.model)],['calls',r=>r.calls],['$',r=>r.cost]]);
  $('#esc').innerHTML=tbl(R.events.slice().reverse(),[['event',r=>r.event],['role',r=>r.role],['from',r=>short(r.frm)],['to',r=>short(r.to)],['why',r=>esc(r.why)]]);
  $('#tables').innerHTML=Object.entries(R.tables).map(([j,rows])=>'<div><b>'+j+'</b>'+tbl(rows,[['model',r=>short(r.model)],['benchmark prior',r=>r.bench],['posterior',r=>r.post],['tasks seen',r=>r.n]])+'</div>').join('')}
 if(tab==='harness'){const H=await (await fetch('/api/harness')).json();$('#hact').textContent=H.active;
  $('#hv').innerHTML=H.versions.map(v=>'<span class="pill ok">v'+v+'</span>').join(' → ')+(H.trial?' → <span class="pill tr">trial: '+esc(H.trial.edit.file)+'</span>'+diff(H.trial.diff)+'<i>'+esc(H.trial.edit.hypothesis)+'</i>':'');
  $('#ha').innerHTML=H.archive.slice().reverse().map(a=>'<div class="box" style="margin-bottom:6px"><span class="pill '+(a.status==='accepted'?'ok':a.status==='rejected'?'bad':'tr')+'">'+a.status+'</span> '+esc((a.files||[a.file]).join(', '))+' — '+esc((a.hypotheses||[a.hypothesis]).join('; '))+(a.reason?' <i>('+esc(a.reason)+')</i>':'')+(a.diff?diff(a.diff):'')+'</div>').join('')||'<i>no edits yet</i>';
  $('#hf').innerHTML=Object.entries(H.files).map(([k,v])=>'<b>'+esc(k)+'</b><pre>'+esc(v)+'</pre>').join('')}
 if(tab==='brain'){const B=await (await fetch('/api/brain')).json();const bs=B.bullets;
  $('#bc').textContent='('+bs.filter(b=>!b.retired&&!b.quarantined).length+' active, '+bs.filter(b=>b.trusted).length+' trusted, '+bs.filter(b=>b.quarantined).length+' quarantined)';
  const ts=bs.map(b=>b.created).sort(),W=520,H=90;if(ts.length){const t0=ts[0],t1=Math.max(ts[ts.length-1],t0+1);
   $('#growth').innerHTML='<polyline fill="none" stroke="#76b900" stroke-width="2.5" points="'+ts.map((t,i)=>((t-t0)/(t1-t0)*(W-20)+10)+','+(H-10-(i+1)/ts.length*(H-20))).join(' ')+'"/><text x="10" y="12" font-size="11">lessons over time: '+ts.length+'</text>'}
  $('#bl').innerHTML=tbl(bs.slice().reverse(),[['#',b=>b.id],['type',b=>b.type],['scope',b=>esc(b.scope)],['lesson',b=>(b.quarantined?'<span class="pill q">QUARANTINED</span>':'')+(b.retired?'<s>':'')+esc(b.text)],['+/-',b=>b.helpful+'/'+b.harmful],['',b=>b.trusted?'<span class="pill ok">trusted</span>':'']]);
  $('#bv').innerHTML=tbl(B.validations.slice().reverse(),[['',v=>'<span class="pill '+(v.worked?'ok':'bad')+'">'+(v.worked?'worked':'failed')+'</span>'],['kind',v=>v.kind],['scope',v=>esc(v.scope)],['detail',v=>esc(v.detail)]]);
  $('#blog').innerHTML=tbl(B.log,[['event',l=>esc(l.event)]])}
}catch(e){console.log(e)}}
setInterval(refresh,4000);refresh();poll();
</script></body></html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "7860")), threaded=True)
