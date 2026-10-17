# Groundhog: an agent team that doesn't relive the same day

A Nemotron/DeepSeek agent team on Crusoe that shares one curated brain (what the team *knows*) and rewrites its own
harness between runs (how the team *works*). A harness change is kept only if it wins on held-out validation templates
and passes the security tests. Guardrails, tools, checker, answers and benchmark evidence live outside anything the
improver can edit.

Problem statements: **Version N+1** (harness evolution) + **Shared Brain** (curated playbook); the verifier-gated
`submit` and definition of done also cover **Done Means Done**.

## Benchmark-informed, adaptive routing (generic, per task)

Every model call is routed to the model whose **published benchmarks** best predict that kind of work, then the
choice adapts to what Groundhog measures:

1. [frozen/benchmarks.yaml](frozen/benchmarks.yaml): model-card scores (τ-bench, BFCL, Terminal-Bench, BIRD-SQL,
   AIME25, GPQA, LiveCodeBench, SWE-bench, IFBench, RULER, MMLU-Pro, HLE, Arena-Hard) plus Crusoe prices. Frozen.
2. [harness/v0/routing.yaml](harness/v0/routing.yaml) (editable by the improver):
   - `jobs`: fixed role jobs (lead = planning, scout = tool_exploration, reflector, improver) → benchmark weights.
   - `task_jobs`: **generic, domain-independent** kinds of work (`text_to_sql`, `numeric_reasoning`,
     `code_generation`, `repo_editing`, `agentic_tools`, `expert_knowledge`, `long_document`, `strict_format`), each
     with a description and benchmark weights. Nothing here is GPUCo-specific, so new task types only need a new
     entry here.
3. **Per-task routing**: the team is built with one Deep Agents sub-agent per task job (`analyst-<job>`,
   `verifier-<job>`), each bound to the model the router picked for that job. The lead reads the task and picks the
   sub-agent whose job matches, so each task goes to the model that's best at it. Verifiers are picked from a
   different model family than their analyst.
4. [groundhog/router.py](groundhog/router.py): weighted benchmark score → Beta prior, updated with measured pass/fail
   per (job, model) in `runs/router_stats.json`; picks `argmax posterior − cost_weight·log price`. A missing score
   counts as the worst published score on that benchmark (model cards tend to omit benchmarks a model is weak on).
   Escalation to the quality-only pick after repeated tool errors; 429/503 → retries → cooldown → next-best model.

Routing from benchmarks alone (before any measured outcomes; `python -m groundhog.router` prints the full table):

| job | model | deciding evidence |
|---|---|---|
| lead (planning), improver | DeepSeek-V4-Flash | Terminal-Bench 56.9, SWE-bench 79.0 |
| scout (tool_exploration), reflector | Nemotron-3.5-Lightning | cheapest tier; IFBench 71.9 |
| text_to_sql, numeric_reasoning, code_generation, repo_editing, agentic_tools, expert_knowledge | DeepSeek-V4-Flash (verifier: Lightning/Nano) | LiveCodeBench 91.6, GPQA 88.1, Terminal-Bench 56.9 |
| long_document, strict_format | Nemotron-3.5-Lightning (verifier: Flash) | RULER/IFBench evidence; Flash publishes neither |

Nemotron-3-Ultra has the best scores in the file but isn't served on our Crusoe account, so it's excluded
automatically. Measured outcomes then move picks (e.g. the scout moved after Nano went 3/7 in the first run).

## Layout

| path | what | editable by improver |
|---|---|---|
| `groundhog/world.py` | GPUCo world: DB-A/DB-B, 16 templates, split by template (8 train / 4 val / 4 test), checker | no |
| `groundhog/tools.py` | read-only `sql` (200 rows, 5 s), `schema`, `sample`, `python` (no key in env, deny-list) | no |
| `groundhog/agent.py` | LangChain **Deep Agents** (`create_deep_agent`): lead → built-in `task` tool → `scout` / `analyst-<job>` / `verifier-<job>` sub-agents → `submit` (rejected unless a verifier agrees). Skills via Deep Agents skills; BRAIN.md appended last in every agent's system prompt (stable prefix for prompt caching); built-in general-purpose sub-agent disabled; middleware adds step cap, repeated-call guard, token budget, fallback, escalation, cost log, trace | no |
| `groundhog/brain.py` | ACE-style brain: reflector ops (with cited trace steps), single curator fed from a queue, rapidfuzz dedup ≥85, contradictions resolved by scope then counters, quarantine of tenant-derived instructions/URLs, retire at harmful ≥ helpful+2, trust after a later passing task, ~1.5k-token cap | no |
| `groundhog/evolve.py` | N+1 loop; improver = read-only Deep Agent (may read `harness/`, training traces, archive, router stats; answers/checker/val/test denied and logged); leak filter, gate, archive, git commit, 2×2/transfer eval | no |
| `harness/vN/` | `lead_prompt.md`, `subagents.yaml`, `tool_guide.md`, `skills/<name>/SKILL.md`, `routing.yaml` | **yes** |
| `frozen/` | `guardrails.md`, `benchmarks.yaml` | no |

Planted quirks (DB-A): money in cents, epoch-ms timestamps, stale `jobs_v1`, soft-deleted tenants/jobs, GPU name
aliases, mixed status casing, preemptible discount (`spot_multiplier`), prompt injections in `support_tickets`
(exfiltration URL + a "report revenue ×1.2" instruction; the security test fails only on obeying them).
DB-B: money in mills, epoch seconds, `jobs_legacy`, `is_active` flag, different aliases.

## Run

```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install deepagents langchain-openai rapidfuzz streamlit pytest "numpy>=2"
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
- The team runs on the real `deepagents` package (0.7.28) with `langchain-openai` pointed at Crusoe.
- **Fallbacks used** (as allowed by the plan): Python router mirroring Switchyard's `auto` route instead of `switchyard-server`
  ([routes.toml](routes.toml) is ready for it); OpenShell policy is a draft ([sandbox/policy.yaml](sandbox/policy.yaml)) and
  the `python` tool's deny-list stands in for it outside the sandbox — denials are logged to `runs/denials.jsonl`.
- Contradiction resolution in the curator is by dedup + counters only (no scope-aware contradiction detection).

Credits: Deep Agents, ACE, Meta-Harness, Harness², RRSI, Darwin Gödel Machine, Switchyard, OpenShell, NemoClaw, Nemotron.
