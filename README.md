# Groundhog: a computer-use agent team that learns Azure while it works

An NVIDIA Nemotron agent team on Crusoe that operates the **real Azure portal** from screenshots and text, and gets
better at it as it goes. It accumulates **knowledge** (a curated brain), **grows its own harness** (new skills, new
specialist sub-agents, prompt and routing edits, each a git-committed version) and **adapts in the middle of a task**,
driven only by the user's request and the team's own interaction, with no human feedback needed.

Problem statements: **Version N+1** (harness evolution) · **Shared Brain** (curated playbook) · **Done Means Done**
(verifier-gated submit).

## How it works

| layer | what | adapts from |
|---|---|---|
| **Team** (LangChain Deep Agents) | lead → `scout`, `analyst-<job>`, `verifier-<job>` + specialist sub-agents the improver creates | |
| **Screen** | Playwright Chromium; screenshot with numbered marks on every element (Set-of-Marks); act by number | |
| **NeMo Switchyard** | embedded `stage_router`: every prompt → capable or efficient model from the agent's tool activity | each prompt |
| **Adaptive router** | which models fill the tiers per job: published benchmarks (WebVoyager, OSWorld, OCRBench, τ-bench, IFBench) + measured success | every task outcome |
| **Brain** | curated lessons (reflector → single curator: dedup, quarantine, trust, retire), in every agent's prompt | mid-task + after tasks |
| **Harness** `harness_azure/vN/` | prompts, sub-agents (`subagents.yaml`, incl. `specialists`), skills (`skills/*/SKILL.md`), routing, Deep Agents profile | mid-task + after batches |
| **Guardrails** `frozen/`, tools | destructive/submitting clicks need human approval (denied unattended), no passwords, Azure URLs only | nobody |

**Mid-task adaptation (no feedback):** the lead's middleware reports what happens inside a running task (a sub-agent
returns, a guard trips, a model escalates, an unverified submit is blocked, the user steers). A background learner turns
it into brain lessons the running agents read at their next model call, and on trouble asks the improver for one
harness edit applied immediately; new skills are injected into the running task's prompts. After a batch, the team also
learns from outcomes **it judged itself** (verifier-confirmed submit); the grader is used only to report results.

## Models (Crusoe)

All five NVIDIA models served on Crusoe are the default for every role: **Nemotron 3.5 Super VL** and **Nemotron 3 Nano
Omni** (screen), **Nemotron 3 Super**, **Nemotron 3.5 Lightning**, **Nemotron 3 Nano** (planning, reflection,
improvement). One or two alternatives per category fill Switchyard's other tier: Qwen3.8-27B and Gemma-4-31B (vision),
gpt-oss-120b (text).

## Experiment

14 real Azure tasks, 8 long-tail (multi-tab creation wizards filled without submitting, multi-blade read tours, >20
screen actions). **10 training** tasks in 2 batches of 5 (4 long-tail); **4 hard held-out** long-tail tasks evaluated
after **0, 5 and 10** training tasks on a frozen snapshot (harness version, brain and router fixed during the round),
in parallel headless browsers sharing the human's sign-in. Metrics per held-out task: solved (graded on the final
browser state), self-assessed done, screen actions, model calls, tokens, cost, seconds, sub-agent delegations, scout
and specialist use, guards, escalations, fallbacks, tool errors, blocked submits, Switchyard capable/efficient, models,
final screenshot.

```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
echo "LLM_API_KEY=<Crusoe key>" > .env
.venv/bin/python -m groundhog.app            # http://localhost:7860 · "Open Azure portal" → sign in yourself
GROUNDHOG_BROWSERS=10 .venv/bin/python -m groundhog.experiment   # unattended experiment (Results tab updates live)
```

The app: **Architecture** (live system diagram + guided tour), **Live demo** (chat, steer any time, approve risky clicks, 👍/👎 optional), **Harness
versions** (timeline + diffs), **Model routing**, **Knowledge**, **Results** (quantitative and qualitative).

## Results

See the **Results** tab (source: `runs_azure/experiment.jsonl`, `midtask.jsonl`, `archive.jsonl`, `brain.db`,
`routing.jsonl`). Small samples (4 held-out tasks per evaluation): read changes as trends.

## Disclosed

- Only Azure is real; no resources are created (wizards stop at Review + create; Create needs human approval).
- Switchyard is the official `nemo_switchyard` 0.3.0 wheel embedded in-process (no separate server).
- Sandbox: Docker for agent code, Playwright allow-list for the browser; OpenShell policy in `sandbox/policy.yaml` is the target.
- Your Azure session export (`runs_azure/auth_state.json`) and browser profile are git-ignored; never commit them.
