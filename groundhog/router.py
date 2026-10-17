"""Benchmark-informed, adaptive model router (Switchyard `auto`-style escalation, in Python).

score(model, job) = weighted published benchmark scores for the benchmarks that predict that job
                    (role jobs, plus generic task_jobs the lead picks per task, e.g. text_to_sql)
                    (missing score = worst published score on that benchmark)
prior             = Beta(k*score, k*(1-score))  -> updated with measured pass/fail per (job, model) from runs/
choose            = argmax posterior_mean - cost_weight[job] * log10(price / cheapest_price)
escalate          = after N tool errors / repeated calls, re-route ignoring cost ("capable" target)
fallback          = a model that returns 429/503 after retries is cooled down for 5 minutes.
Weights, cost weights, pins and escalation thresholds live in harness/vN/routing.yaml, so the improver can edit them.
"""
import json
import math
import threading
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BENCH = yaml.safe_load((ROOT / "frozen" / "benchmarks.yaml").read_text())["models"]
from .config import HARNESS, RUNS  # noqa: E402
STATS = RUNS / "router_stats.json"
_lock = threading.Lock()
_cooldown: dict[str, float] = {}


def benchmark_scores() -> dict[str, dict[str, float]]:
    """Missing score = 90% of the worst published score on that benchmark: model cards tend to omit benchmarks a model
    is weak on, so published evidence beats no evidence; measured outcomes then correct the prior."""
    benches = {b for m in BENCH.values() for b in m["scores"]}
    worst = {b: min(m["scores"][b] for m in BENCH.values() if b in m["scores"]) for b in benches}
    return {name: {b: m["scores"].get(b, 0.9 * worst[b]) / 100 for b in benches} for name, m in BENCH.items()}


class Router:
    def __init__(self, routing_cfg: dict, available: list[str] | None = None):
        self.cfg = routing_cfg
        self.bench = benchmark_scores()
        avail = available or [m for m, v in BENCH.items() if v["price"][0] is not None]
        from .config import world
        fams = getattr(world(), "ALLOWED_FAMILIES", None)  # frozen per task world (e.g. Nemotron-only for Azure)
        self.models = [m for m in avail if m in BENCH and m not in routing_cfg.get("exclude", [])
                       and (not fams or BENCH[m]["family"] in fams)]

    @staticmethod
    def stats() -> dict:
        return json.loads(STATS.read_text()) if STATS.exists() else {}

    def prior(self, model: str, job: str) -> float:
        w = self.cfg["jobs"].get(job) or self.cfg["task_jobs"][job]["weights"]
        return sum(self.bench[model][b] * x for b, x in w.items()) / sum(w.values())

    def table(self, job: str, capable: bool = False, vision: bool = False) -> list[tuple]:
        """[(utility, model, prior, posterior, n)] best first. vision=True keeps only image-capable models."""
        k, st = self.cfg.get("prior_strength", 4), self.stats().get(job, {})
        lam = 0.0 if capable else self.cfg.get("cost_weight", {}).get(job, self.cfg.get("cost_weight", {}).get("default", 0.15))
        blend = {m: (BENCH[m]["price"][0] * 3 + BENCH[m]["price"][1]) / 4 for m in self.models}
        cheapest = min(blend.values())
        rows = []
        for m in self.models:
            if vision and not BENCH[m].get("vision"):
                continue
            p = self.prior(m, job)
            s, n = st.get(m, [0, 0])
            post = (k * p + s) / (k + n)
            rows.append((post - lam * math.log10(blend[m] / cheapest), m, round(p, 3), round(post, 3), n))
        return sorted(rows, reverse=True)

    def pick(self, role: str, task_job: str | None = None, capable: bool = False, avoid_family: str | None = None,
             avoid_model: str | None = None) -> str:
        """Best model for the role's job. Roles in `vision_roles` only get image-capable models. avoid_family is
        honoured when another family is available; avoid_model (e.g. the analyst's model for a verifier) always."""
        if pin := self.cfg.get("pins", {}).get(role):
            return pin
        rows = self.table(self.job_for(role, task_job), capable, vision=role in self.cfg.get("vision_roles", []))
        fam = self.cfg.get("default_family")  # the default pick stays in this family; capable (escalation) picks may leave it
        if fam and not capable and any(BENCH[m]["family"] == fam for _, m, *_ in rows):
            rows = [r for r in rows if BENCH[r[1]]["family"] == fam] + [r for r in rows if BENCH[r[1]]["family"] != fam]
        other_family = avoid_family and any(BENCH[m]["family"] != avoid_family for _, m, *_ in rows)
        for _, m, *_ in rows:
            if _cooldown.get(m, 0) > time.time() or m == avoid_model or (other_family and BENCH[m]["family"] == avoid_family):
                continue
            return m
        return rows[0][1]

    def tiers(self, role: str, task_job: str | None = None) -> tuple[str, str]:
        """(capable, efficient) models for NeMo Switchyard: capable = best on the job's benchmarks + measured outcomes
        (price ignored); efficient = best cheaper model under the job's cost weight (same model if none is cheaper)."""
        if pin := self.cfg.get("pins", {}).get(role):
            return pin, pin
        cap = self.pick(role, task_job, capable=True)
        eff = self.pick(role, task_job)  # default family (Nemotron) pick
        if eff == cap:  # same model: use the next-best model as the other tier so routing has a real choice
            rows = self.table(self.job_for(role, task_job), vision=role in self.cfg.get("vision_roles", []))
            eff = next((m for _, m, *_ in rows if m != cap and _cooldown.get(m, 0) <= time.time()), cap)
        return cap, eff

    def job_for(self, role: str, task_job: str | None) -> str:
        """Roles with a fixed job use it; task roles (analyst/verifier) use the job the lead tagged for this task."""
        job = self.cfg["roles"][role]
        if job != "task":
            return job
        return task_job if task_job in self.cfg.get("task_jobs", {}) else self.cfg["default_task_job"]

    @staticmethod
    def family(model: str) -> str:
        return BENCH.get(model, {}).get("family", "?")

    @staticmethod
    def cool_down(model: str, seconds: int = 300) -> None:
        _cooldown[model] = time.time() + seconds

    @staticmethod
    def record(job: str, model: str, success: bool) -> None:
        """Measured outcome -> posterior update (persisted, shared by all runs)."""
        with _lock:
            st = Router.stats()
            s, n = st.setdefault(job, {}).get(model, [0, 0])
            st[job][model] = [s + int(success), n + 1]
            STATS.parent.mkdir(exist_ok=True)
            STATS.write_text(json.dumps(st, indent=1))


if __name__ == "__main__":
    cfg = yaml.safe_load((HARNESS / "v0" / "routing.yaml").read_text())
    r = Router(cfg)
    for job in [*cfg["jobs"], *cfg["task_jobs"]]:
        print(f"\n{job}:")
        for u, m, p, post, n in r.table(job):
            print(f"  {u:+.3f}  {m:45s} bench={p:.3f} post={post:.3f} n={n}")
