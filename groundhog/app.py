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
import json
import os
import queue
import re
import sqlite3
import threading
import time

os.environ.setdefault("GROUNDHOG_WORLD", "azure")

import yaml  # noqa: E402
from flask import Flask, jsonify, request, send_file  # noqa: E402

from .agent import Team, load_harness  # noqa: E402
from .brain import Brain  # noqa: E402
from .config import HARNESS, RUNS, world  # noqa: E402
from .evolve import current_version, learn, propose, remember_validations  # noqa: E402
from .evolve import new_version as _new_version  # noqa: E402
from .human import Human  # noqa: E402
from .llm import LLM  # noqa: E402
from .router import BENCH, Router  # noqa: E402

W, app, human, llm = world(), Flask(__name__), Human(), LLM()
brain = Brain(RUNS / "brain.db")
state = {"busy": False, "task": None, "adapting": False}
ACTIONS = {"click", "type_text", "press_key", "go_to_url", "scroll", "observe", "read_page", "wait"}


LESSON_PROMPT = """You maintain the playbook of an agent team that operates the Azure portal. The human operator just typed
the message(s) below while the team was working. Turn them into AT MOST 2 short, general lessons the team should
follow on every future task (not specific to this one run). Return ONLY JSON:
{"ops":[{"op":"add","type":"recipe|pitfall|check|fact","scope":"<portal area or topic>","text":"..."}]}
Return {"ops":[]} if the message carries nothing reusable (e.g. "yes", "ok", or a one-off value)."""

learn_q: queue.Queue = queue.Queue()
harness_lock = threading.Lock()


def note(kind: str, title: str, text: str = "", **extra) -> None:
    human.emit({"role": "system", "kind": kind, "title": title, "text": text, **extra})


def harness_dir() -> tuple:
    v = current_version()
    return str(HARNESS / f"v{v}"), f"v{v}"


def new_version(edit: dict, reason: str) -> None:
    rec = _new_version(edit, reason, brain)
    note("harness", f"Harness v{rec['version']}: updated {edit['file']}", edit["hypothesis"], diff=rec["diff"],
         version=rec["version"], reason=reason)


def improve(note_text: str, why: str, task_id: str) -> None:
    base = HARNESS / f"v{current_version()}"
    state["adapting"] = True
    note("thinking", "Improver is updating the harness", why)
    try:
        edits = propose(llm, base, {"summary": {"tag": "live"}, "results": [dict(task_id=task_id, passed=False)]},
                        n_edits=1, note=note_text)
    finally:
        state["adapting"] = False
    if edits:
        new_version(edits[0], why)
    else:
        note("info", "No harness change", "The improver found no edit that passed the safety and leak filters.")


def snapshot(task_id: str, question: str) -> None:
    """In-progress trace on disk, so the improver can read what happened so far."""
    team = state.get("team")
    trace = list(getattr(team, "st", {}).get("trace", [])) if team else []
    d = RUNS / "traces" / "live"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task_id}.json").write_text(json.dumps(dict(task_id=task_id, question=question, trace=trace), default=str, indent=1))


def learn_from_human(items: list) -> None:
    """Runs while the task continues: (1) curated lesson into the brain now (agents see it at their next model call),
    (2) improver edit applied as a new harness version."""
    t = state.get("task") or {}
    task_id, question = t.get("id", "live-chat"), t.get("text", "")
    msgs = [m for m, _ in items]
    team = state.get("team")
    recent = list(getattr(team, "st", {}).get("trace", []))[-10:] if team else []
    ctx = "\n".join(f"{e.get('role')}/{e.get('kind')} {e.get('tool', '')} {json.dumps(e.get('args', ''), default=str)[:90]} -> "
                    f"{str(e.get('result') or e.get('text') or '')[:140]}" for e in recent)
    hdir, _ = harness_dir()
    router = Router(load_harness(__import__("pathlib").Path(hdir))["routing"])
    msg, _ = llm.chat(router.pick("reflector"), [{"role": "system", "content": LESSON_PROMPT}, {"role": "user", "content":
                      f"TASK: {question}\nRECENT AGENT ACTIONS:\n{ctx}\nHUMAN MESSAGE(S):\n" + "\n".join(msgs)}],
                      max_tokens=1200, meta=dict(role="reflector", task=task_id))
    m = re.search(r"\{.*\}", msg.content or "", re.S)
    try:
        ops = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        ops = {}
    events = brain.apply(ops, task_id, True, [], [dict(step=0, result=x) for x in msgs]) if ops.get("ops") else []
    for e in events:
        note("lesson" if not e.startswith("QUARANTINE") else "quarantine",
             "Lesson learned from you" if e.startswith("add") else e.split(":")[0], e.split(":", 1)[-1].strip())
    snapshot(task_id, question)
    improve(f"LIVE: while working on {question!r} the human said: " + " | ".join(msgs) +
            ". Encode the human's intent as ONE harness edit (prompt, sub-agent prompt, skill or routing) so future tasks "
            "follow it without being told. Keep it general (no task ids).", "learned from your message: " + " | ".join(msgs)[:200],
            task_id)


def learner() -> None:
    while True:
        items = [learn_q.get()]
        time.sleep(1.5)  # coalesce a burst of messages into one update
        while not learn_q.empty():
            items.append(learn_q.get_nowait())
        try:
            learn_from_human(items)
        except Exception as e:  # learning must never break the session
            note("error", "Learning failed", f"{type(e).__name__}: {e}"[:300])


threading.Thread(target=learner, daemon=True, name="learner").start()


def run_task(text: str) -> None:
    task_id = f"live-{int(time.time())}"
    state.update(busy=True, task=dict(id=task_id, text=text, t0=time.time(), n0=len(human.events)), live_learned=False)
    try:
        hdir, label = harness_dir()
        h = load_harness(__import__("pathlib").Path(hdir))
        task = dict(id=task_id, template="live", question=text)
        note("task", f"Task started on harness {label}", text, version=label)
        used = brain.ids_in_render()
        team = Team(h, brain.render, llm, trace_dir=RUNS / "traces" / "live", human=human, step_cap=24, task_timeout_s=1500)
        state["team"] = team
        res = team.run(task)
        res.update(brain_used=used, question=text)
        n_actions = sum(1 for e in res["trace"] if e.get("tool") in ACTIONS)
        verdict = human.ask(f"Finished: {res['answer']!r} after {n_actions} screen actions "
                            f"(now on '{(res['final'] or {}).get('title', '')}'). Did it do the task correctly?", kind="verdict") or ""
        res["passed"] = verdict.strip().lower().startswith(("y", "\U0001f44d"))
        res["trace"].append(dict(step=len(res["trace"]), role="human", kind="verdict", text=verdict))
        (RUNS / "traces" / "live" / f"{task_id}.json").write_text(json.dumps(res, default=str, indent=1))
        before = {j: dict(v) for j, v in Router.stats().items()}
        for role, models in res.get("used", {}).items():  # router learns from this interaction
            for m in models:
                Router.record(res["jobs"][role], m, res["passed"])
        r = Router(h["routing"])
        moves = []
        for role, models in res.get("used", {}).items():
            job = res["jobs"][role]
            for m in models:
                s0, n0 = before.get(job, {}).get(m, [0, 0])
                s1, n1 = Router.stats()[job][m]
                k, p = r.cfg.get("prior_strength", 4), r.prior(m, job) if m in r.bench else 0
                moves.append(f"{job}: {short(m)} {(k * p + s0) / (k + n0):.2f} -> {(k * p + s1) / (k + n1):.2f}")
        note("router", f"Router learned from your {'thumbs up' if res['passed'] else 'thumbs down'}", "\n".join(sorted(set(moves))))
        learn(brain, llm, Router(h["routing"]), [res])
        remember_validations(brain, [res], "live")
        for e in res.get("brain_events", []):
            note("lesson", "Lesson from this task" if e.startswith("add") else e.split(":")[0], e.split(":", 1)[-1].strip())
        if not res["passed"]:
            improve(f"LIVE TASK {text!r} FAILED (human: {verdict}). Make ONE edit that fixes what went wrong.",
                    f"you marked the task as failed ({verdict})"[:200], task_id)
        elif n_actions >= 8 and not state.get("live_learned"):
            improve(f"LIVE TASK {text!r} SUCCEEDED but needed {n_actions} screen actions. Write or update ONE skill "
                    "(skills/<name>/SKILL.md with Deep Agents frontmatter) capturing the procedure that worked, so next time "
                    "it takes fewer steps.", f"long task succeeded in {n_actions} actions: saving the procedure as a skill", task_id)
    except Exception as e:  # keep the session alive; the error is shown in the chat
        note("error", "Error", f"{type(e).__name__}: {e}"[:500])
    finally:
        state.update(busy=False, team=None)


def short(m: str) -> str:
    return str(m).split("/")[-1]


@app.post("/send")
def send():
    text = (request.json or {}).get("text", "").strip()
    if not text:
        return jsonify(ok=False)
    if human.waiting or state["busy"]:
        waiting_kind = human.waiting_kind
        kind = human.say(text)
        if kind == "steer" or waiting_kind == "question":  # learn from guidance now; not from yes/no clicks
            state["live_learned"] = True
            learn_q.put((text, kind))
        return jsonify(ok=True, kind=kind)
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
    """The live browser while a live task runs, otherwise the most recent screen of any (experiment) browser."""
    d = RUNS / "screens"
    live = d / "latest.jpg"
    shots = sorted(d.glob("*.jpg"), key=lambda f: f.stat().st_mtime) if d.exists() else []
    p = live if state["busy"] and live.exists() else (shots[-1] if shots else None)
    return send_file(p, mimetype="image/jpeg", max_age=0) if p else ("", 204)


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
    changes = {a["version"]: a for a in _jl("archive.jsonl") if a.get("status") == "accepted" and "version" in a}
    timeline = [dict(version=v, change=changes.get(v)) for v in versions]
    return jsonify(active=label, versions=versions, timeline=timeline, files=files)


@app.get("/api/recent")
def api_recent():
    """Earlier learning (harness versions and lessons), newest first, so the feed tells the story after a restart."""
    items = [dict(ts=a["ts"], kind="harness", title=f"Harness v{a['version']}: updated {', '.join(a['files'])}",
                  text=a["hypotheses"][0] if a.get("hypotheses") else "", diff=a.get("diff", ""), version=a["version"])
             for a in _jl("archive.jsonl") if a.get("status") == "accepted" and "version" in a]
    items += [dict(ts=b["created"], kind="lesson", title=f"Lesson ({b['type']}, {b['scope']})", text=b["text"])
              for b in brain.bullets() if b.get("created")]
    return jsonify(items=sorted(items, key=lambda x: -x["ts"])[:14])


@app.get("/shots/<name>")
def shot(name):
    p = RUNS / "shots" / os.path.basename(name)
    return send_file(p, mimetype="image/jpeg") if p.exists() else ("", 404)


def _harness_inventory(d) -> dict:
    import pathlib
    d = pathlib.Path(d)
    skills = sorted(p.parent.name for p in d.glob("skills/*/SKILL.md"))
    try:
        sp = yaml.safe_load((d / "subagents.yaml").read_text()) or {}
    except Exception:
        sp = {}
    specs = sorted((sp.get("specialists") or {}).keys())
    return dict(skills=skills, specialists=specs, subagents=["scout", "analyst-<job>", "verifier-<job>", *specs])


@app.get("/api/results")
def api_results():
    ex = _jl("experiment.jsonl")
    curve = []
    for e in ex:
        if e.get("event") != "heldout":
            continue
        pt = e["per_task"]
        lo, sh = [x for x in pt if x["long"]], [x for x in pt if not x["long"]]
        solved = [x for x in pt if x["passed"]]
        curve.append(dict(seen=e["seen"], version=e["version"], n=len(pt), solved=len(solved), long_n=len(lo),
                          long_solved=sum(x["passed"] for x in lo), short_n=len(sh), short_solved=sum(x["passed"] for x in sh),
                          steps=round(sum(x["steps"] for x in pt) / max(1, len(pt)), 1),
                          long_steps=round(sum(x["steps"] for x in lo) / max(1, len(lo)), 1),
                          cost=round(sum(x["cost"] for x in pt), 4),
                          cost_per_solved=round(sum(x["cost"] for x in pt) / len(solved), 4) if solved else None,
                          tokens=e["summary"].get("tokens_per_task"), per_task=pt))
    train = [dict(batch=e["batch"], version=e["version"], solved=e["summary"]["solved"], n=e["summary"]["n"], per_task=e["per_task"])
             for e in ex if e.get("event") == "train"]
    adapt = [e for e in ex if e.get("event") == "adapt"]
    versions = sorted(int(p.name[1:]) for p in HARNESS.glob("v*") if p.name[1:].isdigit())
    growth = [dict(version=v, **_harness_inventory(HARNESS / f"v{v}")) for v in versions]
    edits = [a for a in _jl("archive.jsonl") if a.get("status") == "accepted"]
    bs = brain.bullets(include_hidden=True)
    log = _jl("routing.jsonl")
    calls = [x for x in log if "role" in x and "event" not in x and "error" not in x]
    by_model = {}
    for x in calls:
        m = by_model.setdefault(x["model"], dict(model=x["model"], calls=0, cost=0.0, nemotron="nemotron" in x["model"].lower()))
        m["calls"] += 1
        m["cost"] = round(m["cost"] + x.get("cost", 0), 4)
    sy = [x for x in log if x.get("event") == "switchyard"]
    den = _jl("denials.jsonl")
    kinds = {}
    for d in den:
        k = d["kind"].split(":")[0]
        kinds[k] = kinds.get(k, 0) + 1
    return jsonify(curve=curve, train=train, adapt=adapt, growth=growth, edits=edits[-12:],
                   brain=dict(total=len(bs), active=sum(1 for b in bs if not b["retired"] and not b["quarantined"]),
                              trusted=sum(1 for b in bs if b["trusted"]), quarantined=sum(1 for b in bs if b["quarantined"]),
                              retired=sum(1 for b in bs if b["retired"]), created=sorted(b["created"] for b in bs if b.get("created")),
                              top=sorted([b for b in bs if not b["quarantined"]], key=lambda b: (-b["trusted"], -b["helpful"]))[:8]),
                   models=sorted(by_model.values(), key=lambda m: -m["calls"]),
                   switchyard=dict(capable=sum(1 for x in sy if x["tier"] == "capable"), efficient=sum(1 for x in sy if x["tier"] == "efficient")),
                   escalations=sum(1 for x in log if x.get("event") == "escalate"), fallbacks=sum(1 for x in log if x.get("event") == "fallback"),
                   denials=kinds, spent=round(sum(x.get("cost", 0) for x in log), 3), cached=sum(x.get("cached", 0) for x in calls),
                   started=ex[0]["ts"] if ex else None, done=any(e.get("event") == "done" for e in ex), now=time.time(),
                   tasks={t["id"]: dict(q=t["question"], long=t["long"]) for t in W.ALL.values()})


@app.get("/api/summary")
def api_summary():
    log = _jl("routing.jsonl")
    sy = [x for x in log if x.get("event") == "switchyard"]
    bs = brain.bullets(include_hidden=True)
    vs = brain.validations()
    acc = [a for a in _jl("archive.jsonl") if a.get("status") == "accepted"]
    models = sorted({x["model"] for x in log if "role" in x and "event" not in x and "error" not in x})
    t = state.get("task")
    return jsonify(version=current_version(), versions_learned=len(acc),
                   lessons=sum(1 for b in bs if not b["retired"] and not b["quarantined"]), trusted=sum(1 for b in bs if b["trusted"]),
                   quarantined=sum(1 for b in bs if b["quarantined"]),
                   capable=sum(1 for x in sy if x["tier"] == "capable"), efficient=sum(1 for x in sy if x["tier"] == "efficient"),
                   models=[short(m) for m in models], spent=round(sum(x.get("cost", 0) for x in log), 3),
                   validations_ok=sum(1 for v in vs if v["worked"]), validations_bad=sum(1 for v in vs if not v["worked"]),
                   task=t["text"] if t and state["busy"] else None)


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


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Groundhog · self-improving Azure agent</title><style>
:root{--g:#76b900;--g2:#e9f5d6;--ink:#0f1419;--mut:#5b6573;--line:#e4e7ec;--bg:#f4f6f8;--card:#fff;--cap:#7c3aed;--eff:#0e9f6e}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 "Segoe UI",system-ui,sans-serif;color:var(--ink);background:var(--bg)}
header{display:flex;align-items:center;gap:18px;padding:10px 20px;background:#0b0f14;color:#fff}
.logo{font-weight:800;font-size:20px;letter-spacing:.3px}.logo span{color:var(--g)}.sub{color:#9aa4b1;font-size:13px}
nav{margin-left:auto;display:flex;gap:4px}nav button{background:none;border:0;color:#aeb6c1;font-size:14px;padding:8px 14px;border-radius:8px;cursor:pointer}
nav button.on{background:#1c242e;color:#fff}nav button:hover{color:#fff}
.kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;padding:14px 20px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px}.kpi .v{font-size:24px;font-weight:700}
.kpi .l{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.4px}.kpi .d{font-size:12px;color:var(--mut)}.kpi.hi .v{color:#3d7a00}
.view{display:none;padding:14px 20px 20px}.view.on{display:block}
.live{display:grid;grid-template-columns:30% 1fr 27%;gap:14px;height:calc(100vh - 190px)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;display:flex;flex-direction:column;min-height:0}
.card>h2{margin:0;padding:10px 14px;font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);border-bottom:1px solid var(--line);display:flex;align-items:center;gap:8px}
.scroll{flex:1;overflow:auto;padding:12px}
.msg{max-width:92%;margin:6px 0;padding:8px 11px;border-radius:12px;white-space:pre-wrap;word-break:break-word}
.me{margin-left:auto;background:#0b5cff;color:#fff;border-bottom-right-radius:3px}.me small{color:#cfe0ff}
.ag{background:#f1f3f6;border-bottom-left-radius:3px}.ag small,.chip small{color:var(--mut)}
.who{display:block;font-size:11px;font-weight:600;margin-bottom:2px}
.chip{display:flex;gap:7px;align-items:baseline;font-size:12.5px;color:#2b3440;padding:3px 6px;margin:2px 0;border-left:3px solid var(--line)}
.chip.route{border-color:var(--cap);background:#f6f0ff}.chip.route.eff{border-color:var(--eff);background:#ecfdf5}
.chip.warn{border-color:#f59e0b;background:#fffbeb}.chip.task{border-color:var(--g);background:var(--g2);font-weight:600}
.banner{text-align:center;font-size:12px;color:var(--mut);margin:12px 0}.banner b{color:var(--ink)}
.ask{background:#fff7e0;border:1px solid #f5d27a;border-radius:12px;padding:10px;margin:8px 0;font-weight:600}
.btns{display:flex;gap:10px;margin-top:8px}.big{font-size:22px;padding:6px 16px;border-radius:10px;border:1px solid var(--line);background:#fff;cursor:pointer}.big:hover{background:#f3f3f3}
.compose{border-top:1px solid var(--line);padding:10px;display:flex;gap:8px}.compose input{flex:1;padding:10px 12px;border:1px solid #cfd5dd;border-radius:10px;font-size:14px}
.compose button,.qb{background:var(--ink);color:#fff;border:0;border-radius:10px;padding:0 16px;cursor:pointer}
.quick{display:flex;flex-wrap:wrap;gap:6px;padding:0 10px 10px}.qb{background:#eef1f5;color:var(--ink);padding:6px 10px;font-size:12px}
.screen{position:relative;flex:1;display:flex;align-items:flex-start;justify-content:center;background:#eef1f5;border-radius:0 0 12px 12px;overflow:auto;padding:8px}
.screen img{max-width:100%;max-height:100%}.now{padding:8px 14px;font-size:13px;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center}
.steps{margin-left:auto;background:var(--ink);color:#fff;border-radius:20px;padding:2px 12px;font-weight:700}
.status{font-size:12px;color:var(--mut)}.dot{width:9px;height:9px;border-radius:50%;background:#bbb;display:inline-block}.dot.busy{background:var(--g);animation:p 1s infinite}
@keyframes p{50%{opacity:.3}}
.feed .it{border:1px solid var(--line);border-radius:10px;padding:9px 11px;margin-bottom:9px;background:#fff;animation:in .4s}
@keyframes in{from{transform:translateY(-6px);opacity:0}}
.it .t{font-weight:700;font-size:13px;display:flex;gap:6px;align-items:center}.it .x{font-size:12.5px;color:#36404c;white-space:pre-wrap;margin-top:3px}
.it.harness{border-color:var(--g);background:var(--g2)}.it.lesson{border-color:#93c5fd;background:#eff6ff}.it.router{border-color:#c4b5fd;background:#f5f3ff}
.it.quarantine,.it.error{border-color:#fca5a5;background:#fef2f2}.it.thinking{border-style:dashed}
.badge{display:inline-block;font-size:11px;padding:1px 8px;border-radius:10px;background:#eef1f5;color:#334;font-weight:600}
.b-g{background:var(--g2);color:#3d7a00}.b-p{background:#efe7ff;color:var(--cap)}.b-e{background:#dcfce7;color:#047857}.b-r{background:#fee2e2;color:#b91c1c}.b-y{background:#fef3c7;color:#92400e}
pre.diff{background:#0b0f14;color:#d6dde6;border-radius:8px;padding:8px;font-size:11.5px;white-space:pre-wrap;max-height:260px;overflow:auto;margin:6px 0 0}
pre.diff .a{color:#9be15d}pre.diff .d{color:#ff8a8a}
details summary{cursor:pointer;color:#0b5cff;font-size:12px;margin-top:4px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.pad{padding:14px}h3{margin:0 0 8px;font-size:15px}.muted{color:var(--mut);font-size:13px}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--mut);font-weight:600;font-size:12px}
.bar{height:8px;border-radius:4px;background:#e5e7eb;position:relative;min-width:120px}.bar i{position:absolute;left:0;top:0;bottom:0;border-radius:4px}
.flow{display:grid;grid-template-columns:1fr 40px 1.2fr 40px 1.6fr 40px 1fr;align-items:center;gap:0}
.node{background:#fff;border:2px solid var(--line);border-radius:14px;padding:12px;text-align:center;min-height:110px;display:flex;flex-direction:column;justify-content:center}
.node b{font-size:15px}.node .m{font-size:12px;color:var(--mut);margin-top:4px}.node.g{border-color:var(--g)}.node.p{border-color:var(--cap)}.node.b{border-color:#93c5fd}
.arr{text-align:center;font-size:26px;color:#9aa4b1}.stack{display:flex;flex-direction:column;gap:8px}.stack .node{min-height:0;padding:8px}
.loop{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:18px}.loop .node{min-height:120px}
.ver{display:flex;gap:12px;align-items:flex-start;padding:12px 0;border-bottom:1px solid var(--line)}.ver .n{font-size:20px;font-weight:800;min-width:54px;color:#3d7a00}
.lessoncard{border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin-bottom:8px;background:#fff}
</style></head><body>
<header><div class="logo">Ground<span>hog</span></div><div class="sub">Self-improving computer-use agent for Azure · NVIDIA Nemotron · NeMo Switchyard</div>
<nav><button class="on" data-v="live">Live demo</button><button data-v="how">How it works</button><button data-v="harness">Harness versions</button><button data-v="routing">Model routing</button><button data-v="brain">Knowledge</button><button data-v="results">Results</button></nav></header>
<div class="kpis">
<div class="kpi hi"><div class="l">Harness version</div><div class="v" id="k-ver">v0</div><div class="d" id="k-ver-d"></div></div>
<div class="kpi hi"><div class="l">Lessons learned</div><div class="v" id="k-les">0</div><div class="d" id="k-les-d"></div></div>
<div class="kpi"><div class="l">Switchyard routing</div><div class="v"><span id="k-eff" style="color:var(--eff)">0</span> / <span id="k-cap" style="color:var(--cap)">0</span></div><div class="d">efficient / capable prompts</div></div>
<div class="kpi"><div class="l">Nemotron models</div><div class="v" id="k-mod">0</div><div class="d" id="k-mod-d">all NVIDIA Nemotron on Crusoe</div></div>
<div class="kpi"><div class="l">Validations</div><div class="v"><span id="k-ok" style="color:#047857">0</span> / <span id="k-bad" style="color:#b91c1c">0</span></div><div class="d">worked / failed</div></div>
<div class="kpi"><div class="l">Cost so far</div><div class="v" id="k-cost">$0</div><div class="d">Crusoe inference</div></div></div>

<div class="view on" id="v-live"><div class="live">
<div class="card"><h2>Conversation <span class="status" id="status"><span class="dot" id="dot"></span> idle</span></h2><div class="scroll" id="chat"></div>
<div id="askbox"></div>
<div class="compose"><input id="inp" placeholder="Ask for an Azure task, or steer it while it works..." autocomplete="off"><button id="send">Send</button></div>
<div class="quick"><button class="qb" id="signin">Open Azure portal</button><button class="qb" data-q="Open the list of virtual machines.">Short task</button>
<button class="qb" data-q="Open the form to create a new resource group. Name it groundhog-demo-rg, select the region East US, go to the Tags tab and add a tag with name env and value demo, then open Review + create and read the summary back to me. Do not click Create.">Long task (10+ steps)</button>
<button class="qb" data-q="Go to Subscriptions and open my subscription. Tell me its subscription ID and status. Then open its Access control (IAM), go to Role assignments and tell me how many role assignments there are.">Long read task</button></div></div>
<div class="card"><h2>What the agent sees</h2><div class="now"><span id="now">Waiting for a task</span><span class="steps" id="steps">0 steps</span></div>
<div class="screen"><img id="shot" src="/screen.jpg"></div></div>
<div class="card"><h2>Learning live</h2><div class="scroll feed" id="feed"><div class="muted">Lessons, harness versions and routing updates appear here the moment they happen.</div></div></div>
</div></div>

<div class="view" id="v-how"><div class="card pad"><h3>How a request flows</h3>
<div class="flow"><div class="node b"><b>You</b><div class="m">task in plain text<br>steer any time<br>answer questions<br>approve risky clicks<br>👍 / 👎</div></div><div class="arr">→</div>
<div class="node g"><b>Lead agent</b><div class="m">plans, writes a definition of done, delegates (LangChain Deep Agents)</div><div class="m" id="f-lead"></div></div><div class="arr">→</div>
<div class="stack"><div class="node p"><b>Scout</b><div class="m">explores the portal from the screenshot</div><div class="m" id="f-scout"></div></div>
<div class="node p"><b>Analyst</b><div class="m">clicks and types by numbered marks</div><div class="m" id="f-analyst"></div></div>
<div class="node p"><b>Verifier</b><div class="m">independently checks the screen, different model</div></div></div><div class="arr">→</div>
<div class="node"><b>Azure portal</b><div class="m">real browser, screenshots with numbered marks, safety policy: no passwords, destructive clicks need your approval</div></div></div>
<h3 style="margin-top:22px">What adapts, and from what</h3>
<div class="loop"><div class="node p"><b>NeMo Switchyard</b><div class="m">routes every prompt: errors or exploration → capable model; clean progress → efficient model</div><div class="m" id="f-sy"></div></div>
<div class="node p"><b>Adaptive router</b><div class="m">chooses which Nemotron models fill each tier per job, from published benchmarks (WebVoyager, OSWorld, OCRBench, τ-bench) + measured success from your 👍/👎</div></div>
<div class="node b"><b>Knowledge (brain)</b><div class="m">your messages and every task become curated lessons (dedup, quarantine of injected instructions, trust) in every agent's prompt, immediately</div><div class="m" id="f-brain"></div></div>
<div class="node g"><b>Harness</b><div class="m">the improver turns your guidance and failures into a new version: prompts, sub-agent prompts, skills, routing. Each version is git-committed</div><div class="m" id="f-ver"></div></div></div>
<p class="muted" style="margin-top:16px">Models: Nemotron 3.5 Super VL and Nemotron 3 Nano Omni see the screen; Nemotron 3 Super and Nemotron 3.5 Lightning plan; served by Crusoe. Unattended mode (<code>evolve</code>) runs the same improver against a train/validation/security gate.</p></div></div>

<div class="view" id="v-harness"><div class="grid2"><div class="card pad"><h3>Version history</h3><div class="muted">Every version is a folder <code>harness_azure/vN</code> and a git commit.</div><div id="h-tl"></div></div>
<div class="card pad"><h3>Current harness files (<span id="h-act"></span>)</h3><div id="h-files"></div></div></div></div>

<div class="view" id="v-routing"><div class="grid2"><div class="card pad"><h3>NeMo Switchyard: last routing decisions</h3><div class="muted">One row per prompt. Tier picked from the agent's recent tool activity.</div><div id="r-sy"></div></div>
<div class="card pad"><h3>Which Nemotron models fill the tiers</h3><div id="r-tiers"></div><h3 style="margin-top:16px">Calls and cost per model</h3><div id="r-mix"></div></div></div>
<div class="card pad" style="margin-top:14px"><h3>Adaptive router: published benchmark prior vs measured success</h3><div class="muted">Bars: grey = benchmark prior, green = belief after real outcomes. It moves with every 👍/👎.</div><div class="grid3" id="r-tables"></div></div></div>

<div class="view" id="v-results">
<div class="card pad"><h3>Experiment: does the team get better on Azure tasks it has never seen?</h3>
<div class="muted" id="x-meta"></div>
<div class="grid2" style="margin-top:10px"><div><b>Held-out success vs training tasks seen</b><svg id="x-curve" width="100%" height="230" viewBox="0 0 560 230"></svg>
<div class="muted"><span style="color:#76b900">■</span> all held-out tasks <span style="color:#7c3aed">■</span> long-tail (&gt;20 steps) <span style="color:#0e9f6e">■</span> short</div></div>
<div><b>Before vs after (held-out)</b><div id="x-head"></div></div></div></div>
<div class="grid2" style="margin-top:14px"><div class="card pad"><h3>The harness grew: skills and specialist sub-agents</h3><div id="x-growth"></div></div>
<div class="card pad"><h3>Knowledge accumulated (brain)</h3><svg id="x-brain" width="100%" height="110" viewBox="0 0 560 110"></svg><div id="x-brainstats"></div></div></div>
<div class="grid2" style="margin-top:14px"><div class="card pad"><h3>Model routing (NeMo Switchyard + adaptive router)</h3><div id="x-models"></div></div>
<div class="card pad"><h3>Safety and cost</h3><div id="x-safety"></div></div></div>
<div class="card pad" style="margin-top:14px"><h3>Same held-out task: first evaluation vs latest</h3><div id="x-before"></div></div>
<div class="grid2" style="margin-top:14px"><div class="card pad"><h3>What the improver created (verbatim)</h3><div id="x-edits"></div></div>
<div class="card pad"><h3>From the brain</h3><div id="x-lessons"></div><h3 style="margin-top:12px">Training batches</h3><div id="x-train"></div></div></div>
</div>
<div class="view" id="v-brain"><div class="grid2"><div class="card pad"><h3>Lessons in the shared brain</h3><svg id="growth" width="100%" height="90" viewBox="0 0 600 90" preserveAspectRatio="none"></svg><div id="b-list"></div></div>
<div class="card pad"><h3>Validations: what worked and what failed</h3><div id="b-val"></div></div></div></div>

<script>
const $=s=>document.querySelector(s),$$=s=>document.querySelectorAll(s);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const MODELS={'NVIDIA-Nemotron-3.5-Super-VL':'Nemotron 3.5 Super VL','Nemotron-3-Nano-Omni-Reasoning-30B-A3B':'Nemotron 3 Nano Omni','Nemotron-3.5-Lightning-30B-A3B':'Nemotron 3.5 Lightning','NVIDIA-Nemotron-3-Super-120B-A12B':'Nemotron 3 Super','NVIDIA-Nemotron-3-Nano-30B-A3B':'Nemotron 3 Nano'};
const mn=m=>{const s=String(m||'').split('/').pop();return MODELS[s]||s};
const ROLE={lead:'Lead',scout:'Scout',analyst:'Analyst',verifier:'Verifier',improver:'Improver',reflector:'Reflector'};
const JOB=j=>String(j||'').replace(/^(analyst|verifier)-/,'').replace(/_/g,' ');
$$('nav button').forEach(b=>b.onclick=()=>{$$('nav button').forEach(x=>x.classList.remove('on'));$$('.view').forEach(x=>x.classList.remove('on'));b.classList.add('on');$('#v-'+b.dataset.v).classList.add('on');refresh()});
async function send(t){await fetch('/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})})}
$('#send').onclick=()=>{const t=$('#inp').value.trim();if(t){send(t);$('#inp').value=''}};$('#inp').onkeydown=e=>{if(e.key==='Enter')$('#send').click()};
$$('.qb[data-q]').forEach(b=>b.onclick=()=>send(b.dataset.q));$('#signin').onclick=()=>fetch('/open',{method:'POST'});
const chat=$('#chat'),feed=$('#feed');let after=-1,feedEmpty=true;
function add(el,html,cls){const d=document.createElement('div');d.className=cls;d.innerHTML=html;el.appendChild(d);el.scrollTop=1e9;return d}
function first(r){return String(r||'').split('\n')[0]}
function act(e){const a=e.args||{},r=first(e.result);
 const m={click:['🖱',r.replace(/^Clicked \[\d+\] /,'Clicked ')],type_text:['⌨',`Typed “${a.text||''}”`+(r.match(/into \[\d+\] (.*?)( and pressed Enter)?\.$/)?' into '+r.match(/into \[\d+\] (.*?)( and pressed Enter)?\.$/)[1]:'')],
 scroll:['↕',`Scrolled ${a.direction||''}`],observe:['👁','Looked at the screen'],read_page:['📄','Read the page text'],go_to_url:['🌐','Opened '+(a.url||'')],wait:['⏳','Waited for the page'],press_key:['⌨','Pressed '+(a.key||'')],
 sql:['🗄','Ran SQL'],read_file:['📘','Read '+(a.file_path||'')],ls:['📁','Listed files']}[e.tool];
 if(!m)return null;const warn=/^(ERROR|DENIED|Error)/.test(e.result||'');return [m[0],(warn?e.result:m[1]),warn]}
function render(e){
 const who=`<span class="who">${ROLE[e.role]||e.role}${e.model?' · '+mn(e.model):''}</span>`;
 if(e.role==='human'){if(e.kind==='verdict')return;add(chat,esc(e.text)+(e.kind!=='task'?`<br><small>${e.kind==='steer'?'steering':'answer'}</small>`:''),'msg me');return}
 if(e.kind==='question')return;
 if(e.role==='system'){
  if(e.kind==='task'){add(chat,`<b>${esc(e.title)}</b>`,'banner');return}
  if(e.kind==='info'){add(chat,esc(e.text||e.title),'banner');return}
  const icon={lesson:'🧠',harness:'🛠',router:'📈',thinking:'⚙',quarantine:'🛡',error:'⚠',learned:'🧠'}[e.kind]||'•';
  if(feedEmpty){feed.innerHTML='';feedEmpty=false}
  const it=document.createElement('div');it.className='it '+e.kind;
  it.innerHTML=`<div class="t">${icon} ${esc(e.title||e.kind)}${e.version?` <span class="badge b-g">v${e.version}</span>`:''}</div><div class="x">${esc(e.text)}</div>`+(e.diff?`<details open><summary>what changed</summary>${diffHtml(e.diff)}</details>`:'');
  feed.prepend(it);if(e.kind==='harness'||e.kind==='lesson')add(chat,`${icon} <b>${esc(e.title)}</b>`,'banner');return}
 if(e.kind==='route'){const eff=/efficient/.test(e.text);add(chat,`🔀 <span>Switchyard → <b>${eff?'efficient':'capable'}</b> ${esc(mn((e.text.match(/: (\S+)/)||[])[1]))} for ${ROLE[e.role]}</span>`,'chip route'+(eff?' eff':''));return}
 if(['escalate','fallback','reroute','guard'].includes(e.kind)){add(chat,`⚠ <span>${esc(ROLE[e.role]||e.role)}: ${esc(e.text)}</span>`,'chip warn');return}
 if(e.kind==='steer'||e.kind==='human')return;
 if(e.tool==='task'){const a=e.args||{};add(chat,`➜ <span>Lead → <b>${esc(JOB(a.subagent_type)||a.subagent_type)}</b> ${a.subagent_type&&a.subagent_type.startsWith('verifier')?'(verify)':''}: ${esc((a.description||'').slice(0,160))}</span>`,'chip task');return}
 if(e.tool==='submit'){const ok=!/^REJECTED/.test(e.result||'');add(chat,`${ok?'✅':'⛔'} <span>${ok?'Submitted':'Submit blocked'}: ${esc(ok?(e.args||{}).answer:'no verifier has confirmed yet')}</span>`,'chip'+(ok?'':' warn'));return}
 if(e.tool==='ask_human')return;
 if(e.tool){const a=act(e);if(a)add(chat,`${a[0]} <span>${esc(a[1])} <small>${esc(ROLE[e.role]||'')} · ${esc(mn(e.model))}</small></span>`,'chip'+(a[2]?' warn':''));return}
 if(e.kind==='final'||e.kind==='think'){let t=String(e.text||'').replace(/\*\*/g,'').trim();if(!t)return;add(chat,who+esc(t.length>420?t.slice(0,420)+'…':t),'msg ag')}}
function diffHtml(d){return '<pre class="diff">'+esc(d).split('\n').map(l=>l.startsWith('+')&&!l.startsWith('+++')?'<span class="a">'+l+'</span>':l.startsWith('-')&&!l.startsWith('---')?'<span class="d">'+l+'</span>':l).join('\n')+'</pre>'}
let lastAsk=null;
async function poll(){try{const d=await (await fetch('/events?after='+after)).json();
 for(const e of d.events){after=e.n;render(e);if(e.role!=='system'&&e.role!=='human'&&e.kind!=='question')$('#now').innerHTML=`<b>${esc(ROLE[e.role]||e.role)}</b>&nbsp;${esc(mn(e.model))}${e.tool?' · '+esc(e.tool):''}`}
 $('#steps').textContent=d.steps+' steps';$('#dot').className='dot'+(d.busy||d.adapting?' busy':'');
 $('#status').lastChild.textContent=d.waiting?' waiting for you':d.adapting?' improving harness':d.busy?' working':' idle';
 const key=d.waiting_kind+'|'+d.waiting;if(key!==lastAsk){lastAsk=key;const box=$('#askbox');box.innerHTML='';
  if(d.waiting_kind==='verdict')box.innerHTML=`<div class="ask" style="margin:8px">${esc(d.waiting)}<div class="btns"><button class="big" onclick="send('yes')">👍</button><button class="big" onclick="$('#why').style.display='flex'">👎</button></div><div id="why" style="display:none;gap:6px;margin-top:8px"><input id="whyt" style="flex:1;padding:6px" placeholder="What went wrong? (optional)"><button class="big" style="font-size:14px" onclick="send('no'+($('#whyt').value?': '+$('#whyt').value:''))">Send</button></div></div>`;
  else if(d.waiting_kind==='approval')box.innerHTML=`<div class="ask" style="margin:8px">🛡 ${esc(d.waiting)}<div class="btns"><button class="big" onclick="send('yes')">✅ Approve</button><button class="big" onclick="send('no')">❌ Deny</button></div></div>`;
  else if(d.waiting)box.innerHTML=`<div class="ask" style="margin:8px">❓ ${esc(d.waiting)}<div class="muted" style="font-weight:400">Type your answer below.</div></div>`}
 $('#shot').src='/screen.jpg?t='+Date.now()}catch(e){} setTimeout(poll,1200)}
function bar(v,c){return `<div class="bar"><i style="width:${Math.round(v*100)}%;background:${c}"></i></div>`}
async function refresh(){try{const S=await (await fetch('/api/summary')).json();
 $('#k-ver').textContent='v'+S.version;$('#k-ver-d').textContent=S.versions_learned+' version(s) learned';$('#k-les').textContent=S.lessons;
 $('#k-les-d').textContent=S.trusted+' trusted · '+S.quarantined+' quarantined';$('#k-eff').textContent=S.efficient;$('#k-cap').textContent=S.capable;
 $('#k-mod').textContent=S.models.length;$('#k-mod-d').title=S.models.map(mn).join(', ');$('#k-ok').textContent=S.validations_ok;$('#k-bad').textContent=S.validations_bad;$('#k-cost').textContent='$'+S.spent;
 const v=document.querySelector('nav button.on').dataset.v;
 if(v==='how'||v==='routing'){const R=await (await fetch('/api/routing')).json(),T=R.tiers,tp=k=>T[k]?`<span class="badge b-p">capable</span> ${mn(T[k][0])}<br><span class="badge b-e">efficient</span> ${mn(T[k][1])}`:'';
  if(v==='how'){$('#f-lead').innerHTML=tp('lead:');$('#f-scout').innerHTML=tp('scout:');$('#f-analyst').innerHTML=tp('analyst:web_navigation');$('#f-sy').innerHTML=`<b>${S.efficient}</b> efficient · <b>${S.capable}</b> capable so far`;$('#f-brain').innerHTML=`<b>${S.lessons}</b> lessons`;$('#f-ver').innerHTML=`now at <b>v${S.version}</b>`}
  else{$('#r-sy').innerHTML='<table><tr><th>Agent</th><th>Job</th><th>Tier</th><th>Model</th><th>Signal</th></tr>'+R.switchyard.slice().reverse().slice(0,25).map(r=>`<tr><td>${ROLE[r.role]}</td><td>${JOB(r.job)}</td><td><span class="badge ${r.tier==='capable'?'b-p':'b-e'}">${r.tier}</span></td><td>${mn(r.model)}</td><td class="muted">${esc((r.evidence||{}).source||'')}</td></tr>`).join('')+'</table>';
   $('#r-tiers').innerHTML='<table><tr><th>Agent / job</th><th>Capable</th><th>Efficient</th></tr>'+Object.entries(R.tiers).map(([k,t])=>`<tr><td>${ROLE[k.split(':')[0]]} ${JOB(k.split(':')[1])}</td><td>${mn(t[0])}</td><td>${mn(t[1])}</td></tr>`).join('')+'</table>';
   $('#r-mix').innerHTML='<table><tr><th>Agent</th><th>Model</th><th>Calls</th><th>$</th></tr>'+R.mix.map(m=>`<tr><td>${ROLE[m.role]||m.role}</td><td>${mn(m.model)}</td><td>${m.calls}</td><td>${m.cost}</td></tr>`).join('')+'</table>';
   $('#r-tables').innerHTML=Object.entries(R.tables).filter(([j])=>['planning','tool_exploration','web_navigation','screen_reading','form_filling','harness_editing'].includes(j)).map(([j,rows])=>`<div><b>${JOB(j)}</b><table>${rows.map(r=>`<tr><td>${mn(r.model)}<div class="muted">${r.n} real outcome(s)</div></td><td>${bar(r.bench,'#cbd5e1')}${bar(r.post,'#76b900')}<span class="muted">${r.bench} → ${r.post}</span></td></tr>`).join('')}</table></div>`).join('')}}
 if(v==='harness'){const H=await (await fetch('/api/harness')).json();$('#h-act').textContent=H.active;
  $('#h-tl').innerHTML=H.timeline.slice().reverse().map(t=>`<div class="ver"><div class="n">v${t.version}</div><div style="flex:1">${t.change?`<b>${esc(t.change.files.join(', '))}</b> <span class="badge b-g">${esc(t.change.reason||'')}</span><div>${esc(t.change.hypotheses.join('; '))}</div><details><summary>show diff</summary>${diffHtml(t.change.diff||'')}</details>`:'<span class="muted">starting harness</span>'}</div></div>`).join('');
  $('#h-files').innerHTML=Object.entries(H.files).map(([k,c])=>`<details><summary><b>${esc(k)}</b></summary><pre class="diff">${esc(c)}</pre></details>`).join('')}

 if(v==='results'){const X=await (await fetch('/api/results')).json(),C=X.curve;
  $('#x-meta').innerHTML=`20 real Azure portal tasks (8 long-tail) · held-out ${C[0]?C[0].n:8} tasks evaluated after every 4 training tasks · unattended, ${X.done?'finished':'running'} · ${X.started?Math.round((X.now-X.started)/60):0} min · $${X.spent}`;
  const W=560,H=200,px=s=>40+s/12*(W-60),py=f=>H-10-f*(H-30);let g='<line x1="40" y1="190" x2="540" y2="190" stroke="#ccc"/><line x1="40" y1="20" x2="40" y2="190" stroke="#ccc"/>';
  [0,4,8,12].forEach(s=>g+=`<text x="${px(s)-4}" y="205" font-size="11" fill="#666">${s}</text>`);[0,.5,1].forEach(f=>g+=`<text x="8" y="${py(f)+4}" font-size="11" fill="#666">${f*100}%</text>`);
  g+='<text x="230" y="225" font-size="11" fill="#666">training tasks seen</text>';
  const line=(k,n,c)=>{const pts=C.filter(p=>p[n]).map(p=>[px(p.seen),py(p[k]/p[n])]);if(!pts.length)return;g+=`<polyline fill="none" stroke="${c}" stroke-width="3" points="${pts.map(q=>q.join(',')).join(' ')}"/>`+pts.map(q=>`<circle cx="${q[0]}" cy="${q[1]}" r="4" fill="${c}"/>`).join('')};
  line('solved','n','#76b900');line('long_solved','long_n','#7c3aed');line('short_solved','short_n','#0e9f6e');
  C.forEach(p=>g+=`<text x="${px(p.seen)+6}" y="${py(p.solved/p.n)-8}" font-size="11" font-weight="700">${p.solved}/${p.n} · v${p.version}</text>`);$('#x-curve').innerHTML=g;
  const a=C[0],b=C[C.length-1],row=(l,f)=>`<tr><td>${l}</td><td>${a?f(a):'-'}</td><td><b>${b?f(b):'-'}</b></td></tr>`;
  $('#x-head').innerHTML=a?`<table><tr><th></th><th>first (0 seen, v${a.version})</th><th>latest (${b.seen} seen, v${b.version})</th></tr>`+row('held-out solved',p=>p.solved+' / '+p.n)+row('long-tail solved',p=>p.long_solved+' / '+p.long_n)+row('short solved',p=>p.short_solved+' / '+p.short_n)+row('screen actions per task',p=>p.steps)+row('actions per long-tail task',p=>p.long_steps)+row('$ per solved task',p=>p.cost_per_solved??'-')+row('tokens per task',p=>p.tokens)+'</table><div class="muted">n = '+a.n+' held-out tasks per point; small samples, differences of 1-2 tasks are suggestive.</div>':'<i>first evaluation running...</i>';
  $('#x-growth').innerHTML='<table><tr><th>version</th><th>skills</th><th>sub-agents</th></tr>'+X.growth.map(g=>`<tr><td><b>v${g.version}</b></td><td>${g.skills.length} <span class="muted">${esc(g.skills.join(', '))}</span></td><td>${g.subagents.length} <span class="muted">${esc(g.specialists.join(', ')||'scout, analyst, verifier')}</span></td></tr>`).join('')+'</table>';
  const ts=X.brain.created;let bg='';if(ts.length>1){const t0=ts[0],t1=ts[ts.length-1];bg=`<polyline fill="none" stroke="#76b900" stroke-width="3" points="${ts.map((t,i)=>((t-t0)/(t1-t0+1e-9)*530+15)+','+(100-(i+1)/ts.length*85)).join(' ')}"/>`}$('#x-brain').innerHTML=bg+`<text x="15" y="14" font-size="12" fill="#555">${ts.length} lessons over the run</text>`;
  $('#x-brainstats').innerHTML=`<span class="badge b-g">${X.brain.active} active</span> <span class="badge b-g">${X.brain.trusted} trusted</span> <span class="badge b-r">${X.brain.quarantined} quarantined</span> <span class="badge">${X.brain.retired} retired</span>`;
  const tot=X.models.reduce((s,m)=>s+m.calls,0)||1,nem=X.models.filter(m=>m.nemotron).reduce((s,m)=>s+m.calls,0);
  $('#x-models').innerHTML=`<div><span class="badge b-g">${Math.round(nem/tot*100)}% of calls on Nemotron</span> <span class="badge b-e">Switchyard efficient ${X.switchyard.efficient}</span> <span class="badge b-p">capable ${X.switchyard.capable}</span> <span class="badge b-y">escalations ${X.escalations}</span> <span class="badge">fallbacks ${X.fallbacks}</span></div><table style="margin-top:8px">`+X.models.map(m=>`<tr><td>${mn(m.model)}</td><td style="width:45%">${bar(m.calls/tot,m.nemotron?'#76b900':'#94a3b8')}</td><td>${m.calls}</td><td>$${m.cost}</td></tr>`).join('')+'</table>';
  $('#x-safety').innerHTML='<table>'+`<tr><td>Destructive / submitting clicks attempted unattended → denied</td><td><b>${X.denials['azure-guarded']||0}</b></td></tr><tr><td>Navigation outside Azure → denied</td><td><b>${X.denials['azure-egress']||0}</b></td></tr><tr><td>Password fields typed into</td><td><b>0</b> (${X.denials['azure-password-field']||0} attempts denied)</td></tr><tr><td>Improver reads of non-allowed files → denied</td><td><b>${X.denials['improver']||0}</b></td></tr><tr><td>Total cost (Crusoe)</td><td><b>$${X.spent}</b></td></tr><tr><td>Prompt tokens served from cache</td><td><b>${X.cached.toLocaleString()}</b></td></tr><tr><td>Human interventions during the run</td><td><b>0</b></td></tr></table>`;
  if(a&&b){const before=Object.fromEntries(a.per_task.map(x=>[x.task,x]));$('#x-before').innerHTML='<table><tr><th>task</th><th>first</th><th>latest</th></tr>'+b.per_task.map(x=>{const y=before[x.task]||{},T=X.tasks[x.task]||{};const cell=(r,s)=>r?`${r.passed?'✅':'❌'} ${r.steps} actions${r.shot?`<br><a href="/shots/${r.shot}" target="_blank"><img src="/shots/${r.shot}" style="width:210px;border:1px solid #ddd;border-radius:6px"></a>`:''}`:'-';return `<tr><td style="max-width:330px">${T.long?'<span class="badge b-p">long-tail</span> ':''}${esc((T.q||x.task).slice(0,140))}</td><td>${cell(y)}</td><td>${cell(x)}</td></tr>`}).join('')+'</table>'}
  $('#x-edits').innerHTML=X.edits.slice().reverse().map(e=>`<div class="lessoncard"><span class="badge b-g">v${e.version}</span> <b>${esc(e.files.join(', '))}</b><div>${esc(e.hypotheses.join('; '))}</div><div class="muted">${esc(e.reason||'')}</div><details><summary>diff</summary>${diffHtml(e.diff||'')}</details></div>`).join('')||'<i>edits appear after the first training batch</i>';
  $('#x-lessons').innerHTML=X.brain.top.map(b=>`<div class="lessoncard">${b.trusted?'<span class="badge b-g">trusted</span> ':''}<span class="badge">${esc(b.type)}</span> ${esc(b.text)} <span class="muted">(+${b.helpful}/-${b.harmful})</span></div>`).join('')||'<i>lessons appear after the first training batch</i>';
  $('#x-train').innerHTML='<table><tr><th>batch</th><th>harness</th><th>solved</th><th>tasks</th></tr>'+X.train.map(t=>`<tr><td>${t.batch}</td><td>v${t.version}</td><td>${t.solved}/${t.n}</td><td class="muted">${t.per_task.map(x=>(x.passed?'✅':'❌')+' '+x.task.replace('AZ-','')+' ('+x.steps+')').join(' · ')}</td></tr>`).join('')+'</table>'}
 if(v==='brain'){const B=await (await fetch('/api/brain')).json(),bs=B.bullets;
  const ts=bs.map(b=>b.created).sort();if(ts.length>1){const t0=ts[0],t1=ts[ts.length-1];$('#growth').innerHTML=`<polyline fill="none" stroke="#76b900" stroke-width="3" points="${ts.map((t,i)=>((t-t0)/(t1-t0)*580+10)+','+(85-(i+1)/ts.length*75)).join(' ')}"/><text x="10" y="14" font-size="12" fill="#5b6573">${ts.length} lessons over time</text>`}
  $('#b-list').innerHTML=bs.slice().reverse().map(b=>`<div class="lessoncard">${b.quarantined?'<span class="badge b-r">quarantined</span> ':''}${b.trusted?'<span class="badge b-g">trusted</span> ':''}${b.retired?'<span class="badge">retired</span> ':''}<span class="badge">${esc(b.type)}</span> <span class="muted">${esc(b.scope)}</span><div>${b.retired?'<s>':''}${esc(b.text)}</div><div class="muted">helped ${b.helpful} · hurt ${b.harmful}</div></div>`).join('');
  $('#b-val').innerHTML='<table>'+B.validations.slice().reverse().map(x=>`<tr><td><span class="badge ${x.worked?'b-e':'b-r'}">${x.worked?'worked':'failed'}</span></td><td>${esc(x.kind)}</td><td>${esc(x.detail)}</td></tr>`).join('')+'</table>'}
}catch(e){console.log(e)}}
fetch('/api/recent').then(r=>r.json()).then(d=>{if(!d.items.length)return;feed.innerHTML='';feedEmpty=false;
 d.items.forEach(e=>{const icon=e.kind==='harness'?'🛠':'🧠';const it=document.createElement('div');it.className='it '+e.kind;it.style.opacity=.75;
 it.innerHTML=`<div class="t">${icon} ${esc(e.title)}${e.version?` <span class="badge b-g">v${e.version}</span>`:''} <span class="muted" style="margin-left:auto;font-weight:400">earlier</span></div><div class="x">${esc(e.text)}</div>`+(e.diff?`<details><summary>what changed</summary>${diffHtml(e.diff)}</details>`:'');feed.appendChild(it)})});
setInterval(refresh,3000);refresh();poll();
</script></body></html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "7860")), threaded=True)
