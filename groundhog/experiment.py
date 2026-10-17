"""Continual-learning experiment on the real Azure portal (unattended, parallel headless browsers, no human feedback).

Round r: held-out eval (4 hard long-tail tasks) of the CURRENT harness + brain + router, in parallel with training batch r
(5 tasks). Held-out tasks read a frozen snapshot (harness folder fixed, brain rendered at start, router stats untouched
during the round). Training tasks run with the mid-task learner: lessons and harness edits happen WHILE they run,
driven only by the user's request and the team's own interaction. After the round the team also learns from the batch
outcomes it judged itself (verifier-confirmed submit = self-assessed success; the grader is used only for metrics).
Held-out is evaluated after 0, 5 and 10 training tasks.

    GROUNDHOG_BROWSERS=10 python -m groundhog.experiment
"""
import concurrent.futures as cf
import os
import time

os.environ.setdefault("GROUNDHOG_BROWSERS", "10")

import yaml  # noqa: E402

from .agent import load_harness  # noqa: E402
from .brain import Brain  # noqa: E402
from .config import HARNESS, RUNS, world  # noqa: E402
from .evolve import _log, current_version, evaluate, learn, new_version, propose, remember_validations  # noqa: E402
from .live import LiveLearner  # noqa: E402
from .llm import LLM  # noqa: E402
from .router import Router  # noqa: E402

W = world()
NOTE = ("Batch of {n} training tasks just ran on harness {v}; by the team's own verifier {ok}/{n} were completed "
        "(not completed: {failed}). Propose {k} edits that make the team better at tasks like these on the Azure portal: "
        "at least one NEW skill capturing a procedure (what worked, or what went wrong and how to avoid it) and, if a kind "
        "of work recurs (e.g. multi-tab creation wizards, reading values across blades), one NEW specialist sub-agent for "
        "it. Keep them general (no task ids).")


def inventory(v: int) -> dict:
    d = HARNESS / f"v{v}"
    sp = yaml.safe_load((d / "subagents.yaml").read_text()) or {}
    return dict(skills=sorted(p.parent.name for p in d.glob("skills/*/SKILL.md")), specialists=sorted((sp.get("specialists") or {})))


def row(x: dict) -> dict:
    m = x.get("metrics") or {}
    return dict(task=x["task_id"], passed=x["passed"], self_ok=x["answer"] is not None, long=W.ALL[x["template"]]["long"],
                answer=x["answer"], shot=(x.get("final") or {}).get("shot"), infra_error=bool(x.get("infra_error")),
                steps=m.get("actions", 0), **{k: v for k, v in m.items() if k != "actions"})


def main():
    llm, brain = LLM(), Brain(RUNS / "brain.db")
    learner = LiveLearner(brain, llm, max_edits_per_task=1)
    t0 = time.time()
    _log("experiment.jsonl", dict(ts=t0, event="start", batches=W.TRAIN_BATCHES, heldout=W.HELDOUT, version=current_version()))
    for r in range(len(W.TRAIN_BATCHES) + 1):
        v, seen = current_version(), 5 * r
        h = HARNESS / f"v{v}"
        inv = inventory(v)
        with cf.ThreadPoolExecutor(2) as ex:
            jobs = {"heldout": ex.submit(evaluate, h, W.load_tasks("heldout"), f"heldout-after{seen}", brain, False, 4, llm)}
            if r < len(W.TRAIN_BATCHES):
                batch = [W.ALL[t] for t in W.TRAIN_BATCHES[r]]
                jobs["train"] = ex.submit(evaluate, h, batch, f"batch{r + 1}-train", brain, False, 5, llm, learner)
            out = {k: f.result() for k, f in jobs.items()}
        ho = out["heldout"]
        _log("experiment.jsonl", dict(ts=time.time(), event="heldout", seen=seen, version=v, inventory=inv,
                                      summary=ho["summary"], per_task=[row(x) for x in ho["results"]]))
        if "train" not in out:
            break
        tr = out["train"]
        _log("experiment.jsonl", dict(ts=time.time(), event="train", batch=r + 1, version=v, summary=tr["summary"],
                                      per_task=[row(x) for x in tr["results"]]))
        selfj = [dict(x, passed=x["answer"] is not None) for x in tr["results"]]  # the team's own judgement, no grader
        for x in selfj:
            for role, m in x["models"].items():
                Router.record(x["jobs"][role], m, x["passed"])
        learn(brain, llm, Router(load_harness(h)["routing"]), selfj)
        remember_validations(brain, selfj, "train")
        hv = HARNESS / f"v{current_version()}"
        failed = [x["template"] for x in selfj if not x["passed"]]
        note = NOTE.format(n=len(selfj), v=f"v{current_version()}", ok=sum(x["passed"] for x in selfj), failed=", ".join(failed) or "none", k=2)
        edits = propose(llm, hv, dict(tr, results=selfj), n_edits=2, note=note)
        for e in edits:
            new_version(e, f"after training batch {r + 1} (self-assessed {sum(x['passed'] for x in selfj)}/{len(selfj)})", brain)
        _log("experiment.jsonl", dict(ts=time.time(), event="adapt", batch=r + 1, from_version=v, to_version=current_version(),
                                      edits=[dict(file=e["file"], hypothesis=e["hypothesis"]) for e in edits]))
    _log("experiment.jsonl", dict(ts=time.time(), event="done", minutes=round((time.time() - t0) / 60, 1)))


if __name__ == "__main__":
    main()
