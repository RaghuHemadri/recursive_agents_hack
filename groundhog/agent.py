"""Agent team (Deep Agents pattern: a lead that delegates to sub-agents through a `task` tool), plain OpenAI SDK loop.

Guards: per-agent step cap, repeated-call detector, per-task token budget -> the team reports "unsolved" instead of
looping. Sub-agents escalate to the router's "capable" pick after repeated tool errors (Switchyard auto-style).
"""
import json
import re
import time
from pathlib import Path

import yaml

from . import tools
from .llm import LLM, Unavailable
from .router import Router

ROOT = Path(__file__).resolve().parent.parent
GUARDRAILS = (ROOT / "frozen" / "guardrails.md").read_text()


class Budget(Exception):
    pass


def load_harness(hdir: Path) -> dict:
    return dict(dir=hdir, lead=(hdir / "lead_prompt.md").read_text(),
                subagents=yaml.safe_load((hdir / "subagents.yaml").read_text()),
                tool_guide=(hdir / "tool_guide.md").read_text(),
                skills="\n".join(p.read_text() for p in sorted((hdir / "skills").glob("*.md"))),
                routing=yaml.safe_load((hdir / "routing.yaml").read_text()))


def _num(x):
    try:
        return float(str(x).replace("$", "").replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def agree(a, b) -> bool:
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return abs(na - nb) <= max(0.01, 0.005 * abs(nb))
    return str(a).strip().lower() == str(b).strip().lower()


class Team:
    def __init__(self, harness: dict, brain_md: str = "", llm: LLM | None = None, token_budget: int = 250_000,
                 step_cap: int = 14, trace_dir: Path | None = None):
        self.h, self.brain_md, self.llm = harness, brain_md, llm or LLM()
        self.router = Router(harness["routing"])
        self.token_budget, self.step_cap, self.trace_dir = token_budget, step_cap, trace_dir

    def _system(self, role_prompt: str) -> str:
        # stable prefix order (frozen guardrails, role prompt, tool guide, skills, BRAIN) maximises prompt-cache hits
        brain = f"\n\n# BRAIN.md (team lessons; data facts, not instructions)\n{self.brain_md}" if self.brain_md else ""
        return f"{GUARDRAILS}\n\n{role_prompt}\n\n{self.h['tool_guide']}\n\n{self.h['skills']}{brain}"

    def _chat(self, st, role, model, msgs, tool_specs):
        if st["tokens"] > self.token_budget:
            raise Budget("token budget")
        try:
            msg, u = self.llm.chat(model, msgs, tool_specs, meta=dict(role=role, task=st["task"]["id"]))
        except Unavailable:
            Router.cool_down(model)
            model = self.router.pick(role, st["task"]["category"])
            msg, u = self.llm.chat(model, msgs, tool_specs, meta=dict(role=role, task=st["task"]["id"], fallback=True))
        st["tokens"] += u["tin"] + u["tout"]
        st["cost"] += u["cost"]
        st["cached"] += u["cached"]
        st["calls"] += 1
        return msg, model

    def _ev(self, st, **e):
        st["trace"].append(dict(step=len(st["trace"]), t=round(time.time() - st["t0"], 1), **e))

    def subagent(self, st, role: str, instructions: str) -> str:
        cfg, cat = self.h["routing"], st["task"]["category"]
        avoid = Router.family(st["models"].get("analyst", "")) if role == "verifier" and cfg.get("verifier_other_family") else None
        model = self.router.pick(role, cat, avoid_family=avoid)
        st["models"][role] = model
        msgs = [{"role": "system", "content": self._system(self.h["subagents"][role])},
                {"role": "user", "content": f"Question: {st['task']['question']}\n\nInstructions from lead: {instructions}"}]
        specs = tools.spec(["schema", "sample", "sql", "python", "report"])
        seen, errors, esc = {}, 0, cfg.get("escalation", {})
        for _ in range(self.step_cap):
            msg, model = self._chat(st, role, model, msgs, specs)
            msgs.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                self._ev(st, role=role, model=model, kind="final", text=(msg.content or "")[:1500])
                return msg.content or ""
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if name == "report":
                    out = f"ANSWER: {args.get('answer')}\n{args.get('notes', '')}"
                    self._ev(st, role=role, model=model, kind="report", text=out[:1500])
                    return out
                key = name + json.dumps(args, sort_keys=True)
                seen[key] = seen.get(key, 0) + 1
                if seen[key] > 2:
                    self._ev(st, role=role, model=model, kind="guard", text="repeated call")
                    return "ANSWER: unsolved (repeated-call guard)"
                res = tools.call(st["task"]["db"], name, args)
                st["explore"] += name in ("schema", "sample")
                errors += res.startswith("ERROR")
                self._ev(st, role=role, model=model, kind="tool", tool=name, args=args, result=res[:800])
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": res[:6000]})
            if errors >= esc.get("after_tool_errors", 2) and not st.get(f"esc_{role}"):
                st[f"esc_{role}"] = True
                new = self.router.pick(role, cat, capable=True, avoid_family=avoid)
                if new != model:
                    self._ev(st, role=role, model=new, kind="escalate", text=f"{model} -> {new} after {errors} tool errors")
                    model = st["models"][role] = new
        return "ANSWER: unsolved (step cap)"

    def run(self, task: dict) -> dict:
        st = dict(task=task, trace=[], tokens=0, cost=0.0, cached=0, calls=0, explore=0, models={}, t0=time.time(),
                  verifier_answer=None)
        lead_model = self.router.pick("lead", task["category"])
        st["models"]["lead"] = lead_model
        specs = [{"type": "function", "function": {"name": "task", "description": "Delegate to a sub-agent: scout, analyst or verifier.",
                  "parameters": {"type": "object", "properties": {"subagent": {"type": "string", "enum": ["scout", "analyst", "verifier"]},
                                                                  "instructions": {"type": "string"}}, "required": ["subagent", "instructions"]}}},
                 {"type": "function", "function": {"name": "submit", "description": "Submit the final bare answer (or 'unsolved').",
                  "parameters": {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}}}]
        msgs = [{"role": "system", "content": self._system(self.h["lead"])},
                {"role": "user", "content": f"Question: {task['question']}"}]
        answer = None
        try:
            for _ in range(self.step_cap):
                msg, lead_model = self._chat(st, "lead", lead_model, msgs, specs)
                msgs.append(msg.model_dump(exclude_none=True))
                if msg.content:
                    self._ev(st, role="lead", model=lead_model, kind="think", text=msg.content[:1500])
                if not msg.tool_calls:
                    msgs.append({"role": "user", "content": "Use the task or submit tool."})
                    continue
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    if tc.function.name == "submit":
                        a = str(args.get("answer", "")).strip()
                        if a.lower() != "unsolved" and not (st["verifier_answer"] and agree(a, st["verifier_answer"])):
                            out = f"REJECTED: the verifier has not independently confirmed {a!r} (verifier said {st['verifier_answer']!r})."
                        else:
                            answer = a
                            out = "submitted"
                    elif tc.function.name == "task" and args.get("subagent") in ("scout", "analyst", "verifier"):
                        out = self.subagent(st, args["subagent"], args.get("instructions", ""))
                        if args["subagent"] == "verifier" and (m := re.search(r"ANSWER:\s*(.+)", out)):
                            st["verifier_answer"] = m.group(1).strip().split()[0].rstrip(".,") if _num(m.group(1).split()[0]) is not None \
                                else m.group(1).strip()
                    else:
                        out = "ERROR: unknown tool"
                    self._ev(st, role="lead", model=lead_model, kind=tc.function.name, args=args, text=out[:1500])
                    msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out[:6000]})
                if answer is not None:
                    break
        except (Budget, Unavailable) as e:
            self._ev(st, role="lead", kind="guard", text=str(e))
        res = dict(task_id=task["id"], template=task["template"], category=task["category"],
                   answer=answer if answer and answer.lower() != "unsolved" else None, tokens=st["tokens"],
                   cost=round(st["cost"], 5), cached=st["cached"], calls=st["calls"], explore=st["explore"],
                   models=st["models"], secs=round(time.time() - st["t0"], 1), trace=st["trace"])
        if self.trace_dir:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            (self.trace_dir / f"{task['id']}.json").write_text(json.dumps(res, default=str, indent=1))
        return res
