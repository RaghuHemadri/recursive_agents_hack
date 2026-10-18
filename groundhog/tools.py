"""Fixed tools (FROZEN). `python` runs in a Docker sandbox with no network,
no host mounts (answers are not mounted) and no secrets (OpenShell fallback, as the plan allows). Without Docker it falls
back to a subprocess with an empty env plus a deny-list. Every denied/blocked attempt goes to runs/denials.jsonl."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from .config import RUNS

ROW_CAP, SQL_TIMEOUT_S, PY_TIMEOUT_S = 200, 5, 20
SANDBOX_IMAGE = "python:3.12-slim"
DOCKER = shutil.which("docker") and os.environ.get("GROUNDHOG_SANDBOX", "docker") == "docker"
EGRESS = re.compile(r"https?://|socket|urllib|requests|http\.client|smtplib|ftplib", re.I)
PROTECTED = re.compile(r"answers\.json|/eval\b|world\.py|guardrails|\.env\b|LLM_API_KEY", re.I)
DENIALS = RUNS / "denials.jsonl"


def _deny(kind: str, detail: str) -> str:
    DENIALS.parent.mkdir(exist_ok=True)
    with DENIALS.open("a") as f:
        f.write(json.dumps(dict(ts=time.time(), kind=kind, detail=detail[:200])) + "\n")
    return f"DENIED by sandbox policy: {kind}"


def python(code: str) -> str:
    if EGRESS.search(code):  # logged, then still run: the sandbox has no network, so the attempt fails there too
        _deny("egress:" + EGRESS.search(code).group(0), code)
    if PROTECTED.search(code):
        _deny("protected-path:" + PROTECTED.search(code).group(0), code)
        if not DOCKER:
            return "DENIED by sandbox policy: protected path"
    if DOCKER:
        cmd = ["docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--tmpfs", "/tmp:size=16m",
               "--memory", "256m", "--cpus", "1", "--pids-limit", "64", "--user", "65534", SANDBOX_IMAGE,
               "python", "-I", "-"]
        try:
            r = subprocess.run(cmd, input=code, capture_output=True, text=True, timeout=PY_TIMEOUT_S, env={"PATH": os.environ.get("PATH", "")})
        except subprocess.TimeoutExpired:
            return "ERROR: timeout"
        out = r.stdout + r.stderr
        if re.search(r"Network is unreachable|Temporary failure in name resolution|Name or service not known", out):
            _deny("egress-blocked", code)
        return out[-3000:]
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}  # no API key in the child
    with tempfile.TemporaryDirectory() as d:
        try:
            r = subprocess.run([sys.executable, "-I", "-c", code], cwd=d, env=env, capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            return "ERROR: timeout"
    return (r.stdout + r.stderr)[-3000:]
