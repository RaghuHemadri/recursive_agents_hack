"""Mid-task adaptation from the user's intent (the task request) and the team's own interaction, with no human feedback.

The lead's middleware notifies this learner when something informative happens INSIDE a running task: a sub-agent
returns, a guard trips, a model escalates, submit is rejected, or the human steers. One background worker then
  1. writes lessons into the brain (reflector -> curator); running agents read the brain at their next model call;
  2. on trouble (guard / escalation / rejected submit / human steering), asks the improver for ONE harness edit
     (new skill, specialist sub-agent, prompt or routing) applied immediately as harness v{N+1}. New skills are also
     injected into the RUNNING agents' prompts (live_context), so the change helps the task that triggered it.
"""
import json
import queue
import re
import threading
import time
from pathlib import Path

from .config import HARNESS, RUNS
from .router import Router

TROUBLE = {"guard", "escalate", "submit_rejected", "human"}
LESSON_PROMPT = """You maintain the playbook of an agent team that operates the Azure portal. The team is IN THE MIDDLE of
a task. From the user's request and what the team just did, write AT MOST 2 short, general lessons that would help on
this and future tasks (a procedure that worked, a pitfall just hit, how to recognise a screen). Not specific values of
this one run. Return ONLY JSON: {"ops":[{"op":"add","type":"recipe|pitfall|check|fact","scope":"<portal area>",
"text":"..."}]} or {"ops":[]} if nothing reusable happened."""


def live_context(brain, start_dir: Path):
    """Callable for SkillsThenBrain: current brain + any skills the improver added after this task started."""
    start = {p.parent.name for p in start_dir.glob("skills/*/SKILL.md")}

    def render() -> str:
        from .evolve import current_version
        latest = HARNESS / f"v{current_version()}"
        new = [p for p in sorted(latest.glob("skills/*/SKILL.md")) if p.parent.name not in start]
        extra = "".join(f"\n\n## New skill learned during this run: {p.parent.name}\n{p.read_text()[:2500]}" for p in new)
        return brain.render() + extra
    return render


class LiveLearner:
    def __init__(self, brain, llm, on_event=None, max_edits_per_task: int = 2, improve: bool = True):
        self.brain, self.llm, self.on_event, self.max_edits, self.improve = brain, llm, on_event, max_edits_per_task, improve
        self.q: queue.Queue = queue.Queue()
        self.edits: dict[str, int] = {}
        self.last_lesson: dict[str, float] = {}
        threading.Thread(target=self._work, daemon=True, name="live-learner").start()

    def notify(self, st: dict, kind: str, detail: str) -> None:
        task = st.get("task") or {}
        self.q.put(dict(task_id=task.get("id", "?"), intent=task.get("question", ""), kind=kind, detail=detail[:600],
                        trace=list(st.get("trace", []))[-14:], step=len(st.get("trace", [])), ts=time.time()))

    def _emit(self, kind: str, title: str, text: str = "", **extra) -> None:
        rec = dict(ts=time.time(), kind=kind, title=title, text=text, **extra)
        with (RUNS / "midtask.jsonl").open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        if self.on_event:
            self.on_event(rec)

    def _work(self) -> None:
        while True:
            item = self.q.get()
            try:
                self._handle(item)
            except Exception as e:  # mid-task learning must never break a running task
                self._emit("error", "Mid-task learning failed", f"{type(e).__name__}: {e}"[:300], task=item["task_id"])

    def _handle(self, it: dict) -> None:
        tid = it["task_id"]
        ctx = "\n".join(f"{e.get('role')}/{e.get('kind')} {e.get('tool', '')} {json.dumps(e.get('args', ''), default=str)[:100]}"
                        f" -> {str(e.get('result') or e.get('text') or '')[:160]}" for e in it["trace"])
        trouble = it["kind"] in TROUBLE
        # 1. lessons (at most one reflection per task per 40 s unless something went wrong)
        if trouble or time.time() - self.last_lesson.get(tid, 0) > 40:
            self.last_lesson[tid] = time.time()
            from .agent import load_harness
            from .evolve import current_version
            router = Router(load_harness(HARNESS / f"v{current_version()}")["routing"])
            msg, _ = self.llm.chat(router.pick("reflector"), [
                {"role": "system", "content": LESSON_PROMPT},
                {"role": "user", "content": f"USER REQUEST: {it['intent']}\nWHAT JUST HAPPENED ({it['kind']}): {it['detail']}\n"
                                            f"RECENT ACTIONS:\n{ctx}"}], max_tokens=1200, meta=dict(role="reflector", task=tid))
            m = re.search(r"\{.*\}", msg.content or "", re.S)
            try:
                ops = json.loads(m.group(0)) if m else {}
            except json.JSONDecodeError:
                ops = {}
            if ops.get("ops"):
                for ev in self.brain.apply(ops, tid, True, [], it["trace"]):
                    if ev.startswith(("add", "QUARANTINE")):
                        self._emit("lesson" if ev.startswith("add") else "quarantine", f"Mid-task lesson ({it['kind']})",
                                   ev.split(":", 1)[-1].strip(), task=tid, at_step=it["step"])
        # 2. harness edit on trouble, applied immediately
        if not (self.improve and trouble and self.edits.get(tid, 0) < self.max_edits):
            return
        self.edits[tid] = self.edits.get(tid, 0) + 1
        from .evolve import current_version, new_version, propose
        snap = RUNS / "traces" / "midtask-train"
        snap.mkdir(parents=True, exist_ok=True)
        (snap / f"{tid}.json").write_text(json.dumps(dict(task_id=tid, question=it["intent"], trace=it["trace"]), default=str, indent=1))
        base = HARNESS / f"v{current_version()}"
        self._emit("thinking", "Improver adapting the harness mid-task", f"{it['kind']}: {it['detail'][:200]}", task=tid)
        note = (f"MID-TASK: the team is working on {it['intent']!r} and just hit: {it['kind']} - {it['detail'][:300]}. "
                "Make ONE edit that helps right now and on similar tasks: preferably a NEW skill with the procedure for this "
                "kind of screen/wizard, or a NEW specialist sub-agent if this kind of work recurs. General, no task ids.")
        edits = propose(self.llm, base, {"summary": {"tag": "midtask-train"}, "results": [dict(task_id=tid, passed=False)]},
                        n_edits=1, note=note)
        for e in edits:
            rec = new_version(e, f"mid-task ({it['kind']}) on {tid}", self.brain)
            self._emit("harness", f"Harness v{rec['version']}: updated {e['file']} (mid-task)", e["hypothesis"],
                       diff=rec["diff"], version=rec["version"], task=tid, at_step=it["step"])
