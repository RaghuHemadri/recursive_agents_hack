"""NeMo Switchyard (embedded libsy, `stage_router` = the Execution routing behind Switchyard's `auto` preset) decides,
for every model call, whether the prompt goes to the CAPABLE or the EFFICIENT model, from the agent's recent tool
activity (errors / spinning / exploring -> capable; clean progress -> efficient). Groundhog's adaptive benchmark router
decides WHICH Nemotron models fill those two tiers for each job, and keeps updating that from task outcomes."""
import asyncio

from switchyard.libsy import Step, algorithms

SEMANTICS = {"observe": ["observe", "read_page", "scroll", "wait", "read_file", "schema", "sample"],
             "mutate": ["click", "type_text", "press_key", "go_to_url", "sql", "python", "submit"],
             "plan": ["task", "write_todos", "ask_human"]}


def make(cfg: dict):
    kw = dict(picker=cfg.get("picker", "efficient_first"), confidence_threshold=float(cfg.get("confidence_threshold", 0.5)))
    try:
        return algorithms.stage_router(**kw, tool_semantics=SEMANTICS)
    except Exception:  # older binding without tool_semantics: built-in vocabulary
        return algorithms.stage_router(**kw)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def normalize(messages) -> list[dict]:
    """LangChain messages -> Switchyard's normalized messages (text, tool calls, tool results; images dropped)."""
    out = []
    for m in messages:
        t = getattr(m, "type", "")
        txt = _text(m.content)[:2000]
        if t == "human":
            out.append({"role": "user", "content": [{"type": "text", "text": txt or "(screen)"}]})
        elif t == "ai":
            blocks = [{"type": "text", "text": txt}] if txt else []
            blocks += [{"type": "tool_call", "id": c["id"] or f"c{i}", "name": c["name"], "arguments": c.get("args") or {}}
                       for i, c in enumerate(getattr(m, "tool_calls", []) or [])]
            if blocks:
                out.append({"role": "assistant", "content": blocks})
        elif t == "tool":
            err = txt.lstrip().startswith(("ERROR", "Error", "DENIED", "GUARD"))
            out.append({"role": "tool", "content": [{"type": "tool_result", "tool_call_id": m.tool_call_id, "is_error": err,
                                                      "content": [{"type": "text", "text": txt or "(empty)"}]}]})
    return out or [{"role": "user", "content": [{"type": "text", "text": "(start)"}]}]


def decide(algo, messages, capable: str, efficient: str) -> tuple[str, str, dict]:
    """Returns (model, tier, evidence)."""
    if capable == efficient:
        return capable, "single", {}
    models = {"capable": [capable], "efficient": [efficient], "any": [efficient, capable]}

    async def run():
        async for step in algo.run_stream({"model": "auto", "stream": False, "messages": normalize(messages)}, models):
            match step:
                case Step.Done(done):
                    return done
        return None

    done = asyncio.run(run())
    model = done.selected_model_ids[0] if done and done.selected_model_ids else efficient
    ev = (done.metadata.evidence if done and done.metadata else None) or {}
    return model, "capable" if model == capable else "efficient", dict(ev) if isinstance(ev, dict) else {"evidence": str(ev)}
