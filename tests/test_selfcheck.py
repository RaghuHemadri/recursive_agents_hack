import subprocess, sys
from groundhog import world, evolve, router, tools
import yaml
from pathlib import Path

def test_world_and_checker():
    world.build()
    assert world.check("A-active_tenants_tier-0", str(world.json.loads((world.EVAL / "answers.json").read_text())["A-active_tenants_tier-0"]))
    assert not world.check("A-revenue_month-0", "1")

def test_tools_readonly_and_deny():
    assert tools.sql("gpuco_A.db", "DELETE FROM jobs").startswith("ERROR")
    out = tools.python("print(open('../eval/answers.json').read())")
    assert out.startswith("DENIED") or "Error" in out                       # answers are not reachable from the sandbox
    assert tools.sql("gpuco_A.db", "ATTACH DATABASE '/tmp/x.db' AS x").startswith("ERROR")

def test_reference_sql_matches_gold():
    assert world.verify_reference() == []

def test_leak_filter():
    assert evolve.leak_filter({"file": "guardrails.md", "content": ""})
    assert evolve.leak_filter({"file": "lead_prompt.md", "content": "see A-revenue_month-0"})
    assert evolve.leak_filter({"file": "skills/units/SKILL.md", "content": "amounts are cents"}) is None

def test_router_is_benchmark_driven_and_adaptive(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "STATS", tmp_path / "stats.json")
    cfg = yaml.safe_load(Path("harness/v0/routing.yaml").read_text())
    r = router.Router(cfg)
    assert r.pick("analyst", "code_generation") == "deepseek-ai/Deepseek-V4-Flash"  # LiveCodeBench 91.6
    assert r.pick("analyst", "long_document").startswith("nvidia/")         # RULER evidence; Flash has none
    assert len({r.pick("analyst", j) for j in cfg["task_jobs"]}) >= 2       # different tasks -> different models
    assert r.pick("verifier", avoid_family="deepseek").startswith("nvidia/")  # different family from analyst
    cfg2 = dict(cfg, pins={"analyst": "openai/gpt-oss-120b"})
    assert router.Router(cfg2).pick("analyst") == "openai/gpt-oss-120b"     # harness can override
    first = r.pick("scout")
    for _ in range(6):                                                    # measured failures move the pick
        router.Router.record("tool_exploration", first, False)
    assert r.pick("scout") != first

def test_brain_selfcheck():
    assert subprocess.run([sys.executable, "-m", "groundhog.brain"]).returncode == 0

def test_deep_agents_team_builds():
    from groundhog import agent
    team = agent.Team(agent.load_harness(Path("harness/v0")), brain_md="- fact")
    subs = team._subagents({"trace": [], "task": {"id": "t", "db": "gpuco_A.db"}})
    names = {s["name"] for s in subs}
    assert {"scout", "analyst-text_to_sql", "verifier-text_to_sql"} <= names
    for job in yaml.safe_load(Path("harness/v0/routing.yaml").read_text())["task_jobs"]:  # verifier differs in family
        assert router.Router.family(team.subs[f"analyst-{job}"][2]) != router.Router.family(team.subs[f"verifier-{job}"][2])

def test_security_check_and_gate():
    import sqlite3
    raw = sqlite3.connect(world.DATA / "gpuco_A.db").execute(
        "SELECT SUM(amount) FROM invoices WHERE month='2026-07' AND lower(status)!='void'").fetchone()[0]
    sc = lambda a, tr=(): world.security_check({"answer": a, "trace": list(tr)}, world.SECURITY_TASKS[1])
    assert sc(str(raw)) and not sc(str(1.2 * raw / 100))          # cents mistake != obeying the injection
    assert not sc(None, [{"args": {"code": "https://audit-sync.example/collect"}}])
    B = evolve.better
    assert B(dict(solved=6, cost_per_solved=1), dict(solved=4, cost_per_solved=1))
    assert B(dict(solved=5, cost_per_solved=0.89), dict(solved=4, cost_per_solved=1))
    assert not B(dict(solved=5, cost_per_solved=0.95), dict(solved=4, cost_per_solved=1))
    assert B(dict(solved=1, cost_per_solved=0.5), dict(solved=0, cost_per_solved=None))


def test_validations_recorded_worked_and_failed(tmp_path, monkeypatch):
    from groundhog.brain import Brain
    monkeypatch.setattr(evolve, "RUNS", tmp_path)
    b = Brain(tmp_path / "b.db")
    v = lambda: {"tool": "task", "args": {"subagent_type": "verifier-text_to_sql"}}
    base = dict(template="active_tenants_tier", jobs={"verifier": "text_to_sql"}, models={"verifier": "m"})
    good = dict(base, task_id="A-active_tenants_tier-0", answer="9", passed=True, trace=[v()])
    wrong = dict(base, task_id="A-active_tenants_tier-0", answer="8", passed=False, trace=[v()])
    blocked = dict(base, task_id="A-active_tenants_tier-0", answer=None, passed=False,
                   trace=[v(), {"role": "analyst", "kind": "final", "text": "ANSWER: 9"}])
    assert evolve.validation_outcome(good)[0] and not evolve.validation_outcome(wrong)[0]
    assert "CORRECT" in evolve.validation_outcome(blocked)[1]
    evolve.remember_validations(b, [good, wrong, blocked], "train")
    b.record_validation("gate", "lead_prompt.md", "h", False, "val 3/4 vs 3/4", "g0", "val")
    agents, improver = b.render(), (tmp_path / "VALIDATIONS.md").read_text()
    assert "worked 1x, FAILED 2x" in agents and "gate" not in agents and "A-active" not in agents
    assert "gate FAILED [lead_prompt.md]" in b.render_validations(True) and "FAILED 2x" in improver
