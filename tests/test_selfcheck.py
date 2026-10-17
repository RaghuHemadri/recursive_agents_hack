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
    assert tools.python("open('../eval/answers.json')").startswith("DENIED")

def test_leak_filter():
    assert evolve.leak_filter({"file": "guardrails.md", "content": ""})
    assert evolve.leak_filter({"file": "lead_prompt.md", "content": "see A-revenue_month-0"})
    assert evolve.leak_filter({"file": "skills/x.md", "content": "amounts are cents"}) is None

def test_router_is_benchmark_driven_and_adaptive(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "STATS", tmp_path / "stats.json")
    cfg = yaml.safe_load(Path("harness/v0/routing.yaml").read_text())
    r = router.Router(cfg)
    assert r.pick("scout") == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"     # only model with a BFCL score
    assert r.pick("verifier", avoid_family="deepseek").startswith("nvidia/")  # different family from analyst
    cfg2 = dict(cfg, pins={"analyst": "openai/gpt-oss-120b"})
    assert router.Router(cfg2).pick("analyst") == "openai/gpt-oss-120b"     # harness can override
    for _ in range(6):                                                    # measured failures move the pick
        router.Router.record("tool_exploration", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", False)
    assert r.pick("scout") != "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"

def test_brain_selfcheck():
    assert subprocess.run([sys.executable, "-m", "groundhog.brain"]).returncode == 0
