# Groundhog: an agent team that doesn't relive the same day

A Nemotron/DeepSeek agent team on Crusoe that shares one curated brain (what the team *knows*) and rewrites its own
harness between runs (how the team *works*). A harness change is kept only if it wins on held-out validation templates
and passes the security tests. Guardrails, tools, checker, answers and benchmark evidence live outside anything the
improver can edit.

Problem statements: **Version N+1** (harness evolution) + **Shared Brain** (curated playbook); the verifier-gated
`submit` and definition of done also cover **Done Means Done**.

## Benchmark-informed, adaptive routing

Every role is routed to the model whose **published benchmarks** best predict that job, then the choice adapts to
what Groundhog measures:

1. [frozen/benchmarks.yaml](frozen/benchmarks.yaml): scores from the model cards (τ-bench, BFCL, Terminal-Bench,
   BIRD-SQL, AIME25, GPQA, LiveCodeBench, SWE-bench, IFBench, RULER, MMLU-Pro, HLE) plus Crusoe prices. Frozen.
2. [harness/v0/routing.yaml](harness/v0/routing.yaml): *editable* map from job → benchmark weights, e.g.
   `tool_exploration: {bfcl, tau_bench, ifbench}`, `money_arith: {aime25, gpqa, bird_sql, livecodebench}`,
   plus per-job cost weight, pins, escalation thresholds. The analyst's job is the task's own category
   (`sql_lookup` vs `money_arith`), so each task type goes to the model that's good at it.
3. [groundhog/router.py](groundhog/router.py): benchmark score → Beta prior (strength `prior_strength`), updated with
   measured pass/fail per (job, model) in `runs/router_stats.json`; picks `argmax posterior − cost_weight·log price`.
   Missing scores are imputed conservatively (shrunk toward the benchmark mean, −10%).
   Switchyard-`auto`-style escalation to the capable pick after repeated tool errors; 429/503 → backoff+jitter →
   5-minute cooldown and fallback to the next-best model.

Routing at v0 (`python -m groundhog.router` prints the full table):

| job (role) | model | why (benchmarks) |
|---|---|---|
| planning (lead) | DeepSeek-V4-Flash | Terminal-Bench 56.9, strongest agentic scores served on Crusoe |
| tool_exploration (scout) | Nemotron-3-Nano | only model with a published BFCL (53.8); cheapest |
| sql_lookup / money_arith (analyst) | DeepSeek-V4-Flash | LiveCodeBench 91.6, GPQA 88.1 |
| verification (verifier) | Nemotron-3.5-Lightning | best GPQA/MMLU-Pro outside the analyst's family (must differ) |
| reflection (reflector) | Nemotron-3-Nano | RULER-1M 86.3 at $0.05/M |
| harness_editing (improver) | DeepSeek-V4-Flash | SWE-bench Verified 79.0 |

Nemotron-3-Ultra has the best scores in the file but is not served on our Crusoe account, so it's excluded
automatically (no price). The improver can change weights/pins in `routing.yaml`; the gate decides if that helped.

## Layout

| path | what | editable by improver |
|---|---|---|
| `groundhog/world.py` | GPUCo world: DB-A/DB-B, 16 templates, split by template (8 train / 4 val / 4 test), checker | no |
| `groundhog/tools.py` | read-only `sql` (200 rows, 5 s), `schema`, `sample`, `python` (no key in env, deny-list) | no |
| `groundhog/agent.py` | lead → `task(scout/analyst/verifier)` → `submit` (rejected unless verifier agrees); step cap, repeated-call guard, token budget | no |
| `groundhog/brain.py` | ACE-style brain: reflector ops, single curator, rapidfuzz dedup ≥85, quarantine, retire, trust, 1.5k-token cap | no |
| `groundhog/evolve.py` | N+1 loop, leak filter, gate, security tests, archive, git commit, 2×2/transfer eval | no |
| `harness/vN/` | `lead_prompt.md`, `subagents.yaml`, `tool_guide.md`, `skills/*.md`, `routing.yaml` | **yes** |
| `frozen/` | `guardrails.md`, `benchmarks.yaml` | no |

Planted quirks (DB-A): money in cents, epoch-ms timestamps, stale `jobs_v1`, soft-deleted tenants/jobs, GPU name
aliases, mixed status casing, preemptible discount (`spot_multiplier`), prompt injections in `support_tickets`.
DB-B: money in mills, epoch seconds, `jobs_legacy`, `is_active` flag, different aliases.

## Run

```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install rapidfuzz streamlit pytest
echo "LLM_API_KEY=..." > .env                      # Crusoe key
.venv/bin/python -m groundhog.world                 # build DBs + tasks + answers
.venv/bin/python -m pytest -q tests                 # self-checks (no API calls except none)
.venv/bin/python -m groundhog.evolve evolve --gens 3 --budget-usd 5   # unattended loop (resumable state in runs/)
.venv/bin/python -m groundhog.evolve table          # 2x2 (v0/best x brain off/on) on test + DB-B transfer
.venv/bin/streamlit run groundhog/dashboard.py
```

## Results

See [RESULTS.md](RESULTS.md) (only measured numbers, with sample sizes).

## What's real, what's not (disclosed)

- Only the data is synthetic. All model calls are real Crusoe calls.
- **Fallbacks used** (as allowed by the plan): plain OpenAI-SDK loop in the Deep Agents pattern instead of the
  `deepagents` package; Python router mirroring Switchyard's `auto` route instead of `switchyard-server`
  ([routes.toml](routes.toml) is ready for it); OpenShell policy is a draft ([sandbox/policy.yaml](sandbox/policy.yaml)) and
  the `python` tool's deny-list stands in for it outside the sandbox — denials are logged to `runs/denials.jsonl`.
- Contradiction resolution in the curator is by dedup + counters only (no scope-aware contradiction detection).

Credits: Deep Agents, ACE, Meta-Harness, Harness², RRSI, Darwin Gödel Machine, Switchyard, OpenShell, NemoClaw, Nemotron.
