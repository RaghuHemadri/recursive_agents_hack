"""Re-evaluate the held-out tasks at the three experiment points (after 0, 5, 10 training tasks) under the SAME, corrected
browser policy, using the harness version, brain and router state of each point:
  harness: the saved folder harness_azure/v{N} recorded for that point;
  brain:   lessons created before that point (snapshot by creation time; helpful/harmful counters are end-of-run: approx.);
  router:  stats rebuilt from the self-assessed outcomes of the training tasks run before that point.

    python -m groundhog.reeval <seen>        # one process per point; run the three in parallel
"""
import json
import os
import shutil
import sqlite3
import sys
import time

seen = int(sys.argv[1])
from groundhog.config import RUNS  # noqa: E402

RE = RUNS / "reeval"
RE.mkdir(exist_ok=True)
os.environ["GROUNDHOG_ROUTER_STATS"] = str(RE / f"stats_after{seen}.json")
os.environ.setdefault("GROUNDHOG_BROWSERS", "4")

from groundhog.brain import Brain  # noqa: E402
from groundhog.config import HARNESS, world  # noqa: E402
from groundhog.evolve import _log, evaluate  # noqa: E402
from groundhog.experiment import inventory, row  # noqa: E402
from groundhog.llm import LLM  # noqa: E402

W = world()
ex = [json.loads(x) for x in (RUNS / "experiment.jsonl").read_text().splitlines() if x.strip()]
start = next(e["ts"] for e in ex if e["event"] == "start")
adapt = {e["batch"]: e for e in ex if e["event"] == "adapt"}
version = {0: 0, 5: adapt[1]["to_version"] if 1 in adapt else 0, 10: adapt[2]["to_version"] if 2 in adapt else 0}[seen]
cutoff = {0: start, 5: adapt[1]["ts"], 10: adapt[2]["ts"]}[seen]
batches = {0: [], 5: ["batch1-train"], 10: ["batch1-train", "batch2-train"]}[seen]

# router stats from the self-assessed training outcomes before this point
stats: dict = {}
db = sqlite3.connect(RUNS / "state.db")
for tag, res in db.execute("SELECT tag, res FROM results"):
    if tag in batches:
        r = json.loads(res)
        for role, m in r.get("models", {}).items():
            job = r["jobs"].get(role)
            s, n = stats.setdefault(job, {}).get(m, [0, 0])
            stats[job][m] = [s + int(r["answer"] is not None), n + 1]
(RE / f"stats_after{seen}.json").write_text(json.dumps(stats, indent=1))

# brain snapshot: lessons that existed at this point
snap = RE / f"brain_after{seen}.db"
shutil.copy(RUNS / "brain.db", snap)
with sqlite3.connect(snap) as b:
    b.execute("DELETE FROM bullets WHERE created > ?", (cutoff,))
brain = Brain(snap)

t0 = time.time()
out = evaluate(HARNESS / f"v{version}", W.load_tasks("heldout"), f"reheldout-after{seen}", brain, False, 4, LLM())
_log("experiment.jsonl", dict(ts=time.time(), event="heldout", rerun=True, seen=seen, version=version, inventory=inventory(version),
                              brain_lessons=len(brain.bullets()), summary=out["summary"], per_task=[row(x) for x in out["results"]],
                              minutes=round((time.time() - t0) / 60, 1)))
print(f"after{seen}: v{version}, {len(brain.bullets())} lessons -> {out['summary']['solved']}/{out['summary']['n']}")
