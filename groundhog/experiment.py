"""Continual-learning experiment on the real Azure portal (unattended, parallel headless browsers).

Round r: [held-out eval of the current harness+brain+router (8 tasks, no learning)] in parallel with [training batch r
(4 tasks, no learning yet)]. Both read the same frozen state. Only after both finish does the team learn from the batch:
router stats, reflector -> curator (brain) and improver -> 2 harness edits applied as new versions
(skills, specialist sub-agents, prompts, routing). Held-out is evaluated after every 4 training tasks: 0, 4, 8, 12.

    GROUNDHOG_BROWSERS=12 python -m groundhog.experiment
"""
import concurrent.futures as cf
import os
import time

os.environ.setdefault("GROUNDHOG_BROWSERS", "12")

from .agent import load_harness  # noqa: E402
from .brain import Brain  # noqa: E402
from .config import HARNESS, RUNS, world  # noqa: E402
from .evolve import _log, current_version, evaluate, learn, new_version, propose, remember_validations  # noqa: E402
from .llm import LLM  # noqa: E402
from .router import Router  # noqa: E402

W = world()
NOTE = ("Batch of {n} training tasks just ran on harness {v} ({ok}/{n} solved; failed: {failed}). Propose {k} edits that make "
        "the team better at tasks like these on the Azure portal: at least one NEW skill capturing a procedure (what worked, "
        "or what went wrong and how to avoid it) and, if a kind of work recurs (e.g. multi-tab creation wizards, reading "
        "values across blades), one NEW specialist sub-agent for it. Keep them general (no task ids).")


def main():
    llm, brain = LLM(), Brain(RUNS / "brain.db")
    t0 = time.time()
    _log("experiment.jsonl", dict(ts=t0, event="start", batches=W.TRAIN_BATCHES, heldout=W.HELDOUT, version=current_version()))
    for r in range(len(W.TRAIN_BATCHES) + 1):
        v = current_version()
        h = HARNESS / f"v{v}"
        seen = 4 * r
        jobs = {}
        with cf.ThreadPoolExecutor(2) as ex:
            jobs["heldout"] = ex.submit(evaluate, h, W.load_tasks("heldout"), f"heldout-after{seen}", brain, False, 8, llm)
            if r < len(W.TRAIN_BATCHES):
                batch = [W.ALL[t] for t in W.TRAIN_BATCHES[r]]
                # record=False: router stats are updated only after the round, so the parallel held-out run sees exactly
                # the state after 4*r training tasks (harness, brain AND router frozen during the round)
                jobs["train"] = ex.submit(evaluate, h, batch, f"train-batch{r + 1}", brain, False, 4, llm)
            out = {k: f.result() for k, f in jobs.items()}
        ho = out["heldout"]
        _log("experiment.jsonl", dict(ts=time.time(), event="heldout", seen=seen, version=v, summary=ho["summary"],
                                      per_task=[dict(task=x["task_id"], passed=x["passed"], long=W.ALL[x["template"]]["long"],
                                                     steps=sum(1 for e in x["trace"] if e.get("tool") in ACTIONS), cost=x["cost"],
                                                     secs=x["secs"], shot=(x.get("final") or {}).get("shot"), answer=x["answer"])
                                                for x in ho["results"]]))
        if "train" not in out:
            break
        tr = out["train"]
        _log("experiment.jsonl", dict(ts=time.time(), event="train", batch=r + 1, version=v, summary=tr["summary"],
                                      per_task=[dict(task=x["task_id"], passed=x["passed"], long=W.ALL[x["template"]]["long"],
                                                     steps=sum(1 for e in x["trace"] if e.get("tool") in ACTIONS), cost=x["cost"])
                                                for x in tr["results"]]))
        for x in tr["results"]:  # router learns from the batch now that the round is over
            for role, m in x["models"].items():
                Router.record(x["jobs"][role], m, x["passed"])
        learn(brain, llm, Router(load_harness(h)["routing"]), tr["results"])
        remember_validations(brain, tr["results"], "train")
        failed = [x["template"] for x in tr["results"] if not x["passed"]]
        note = NOTE.format(n=len(tr["results"]), v=f"v{v}", ok=tr["summary"]["solved"], failed=", ".join(failed) or "none", k=2)
        edits = propose(llm, h, tr, n_edits=2, note=note)
        for e in edits:
            new_version(e, f"learned from training batch {r + 1} ({tr['summary']['solved']}/4 solved)", brain)
        _log("experiment.jsonl", dict(ts=time.time(), event="adapt", batch=r + 1, from_version=v, to_version=current_version(),
                                      edits=[dict(file=e["file"], hypothesis=e["hypothesis"]) for e in edits]))
    _log("experiment.jsonl", dict(ts=time.time(), event="done", minutes=round((time.time() - t0) / 60, 1)))


ACTIONS = {"click", "type_text", "press_key", "go_to_url", "scroll", "observe", "read_page", "wait"}

if __name__ == "__main__":
    main()
