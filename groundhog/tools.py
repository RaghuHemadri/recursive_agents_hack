"""Fixed tools (FROZEN). Read-only SQL with row cap + timeout; python runs in a subprocess with no secrets in env."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

from .world import DATA

ROW_CAP, SQL_TIMEOUT_S = 200, 5
DENIALS = DATA.parent / "runs" / "denials.jsonl"


def _deny(kind: str, detail: str) -> str:
    DENIALS.parent.mkdir(exist_ok=True)
    with DENIALS.open("a") as f:
        f.write(json.dumps(dict(ts=time.time(), kind=kind, detail=detail[:200])) + "\n")
    return f"DENIED by sandbox policy: {kind}"


def _conn(db: str) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DATA / db}?mode=ro", uri=True, timeout=2)
    deadline = time.time() + SQL_TIMEOUT_S
    c.set_progress_handler(lambda: int(time.time() > deadline), 10_000)
    return c


def sql(db: str, query: str) -> str:
    try:
        with _conn(db) as c:
            cur = c.execute(query)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(ROW_CAP + 1)
    except Exception as e:  # sqlite errors are shown to the agent
        return f"ERROR: {e}"
    more = f"\n... truncated at {ROW_CAP} rows" if len(rows) > ROW_CAP else ""
    return json.dumps(cols) + "\n" + "\n".join(json.dumps(r) for r in rows[:ROW_CAP]) + more


def schema(db: str) -> str:
    with _conn(db) as c:
        return "\n".join(r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE type='table'"))


def sample(db: str, table: str, n: int = 5) -> str:
    if not table.replace("_", "").isalnum():
        return "ERROR: bad table name"
    return sql(db, f"SELECT * FROM {table} LIMIT {min(int(n), 20)}")


def python(code: str) -> str:
    # shortcut: string deny-list stands in for OpenShell's filesystem/network policy when run outside the sandbox
    for bad in ("answers.json", "/eval", "world.py", "guardrails", "http://", "https://", "socket", "requests", "urllib"):
        if bad in code:
            return _deny("python:" + bad, code)
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}  # no API key in the child
    with tempfile.TemporaryDirectory() as d:
        try:
            r = subprocess.run([sys.executable, "-I", "-c", code], cwd=d, env=env, capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            return "ERROR: timeout"
    return (r.stdout + r.stderr)[-3000:]
