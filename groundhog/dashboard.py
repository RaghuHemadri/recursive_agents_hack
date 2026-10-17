"""Groundhog dashboard: streamlit run groundhog/dashboard.py"""
import json
import sqlite3
import time
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"


def jl(name):
    p = RUNS / name
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


st.set_page_config("Groundhog", layout="wide")
routing, evals, archive, denials = jl("routing.jsonl"), jl("evals.jsonl"), jl("archive.jsonl"), jl("denials.jsonl")
started = float((RUNS / "started_at").read_text()) if (RUNS / "started_at").exists() else time.time()
interventions = len((RUNS / "interventions.log").read_text().splitlines()) if (RUNS / "interventions.log").exists() else 0
version = max((int(p.name[1:]) for p in (ROOT / "harness").glob("v*") if p.name[1:].isdigit()), default=0)
spent = sum(r.get("cost", 0) for r in routing)
st.title("Groundhog")
st.markdown(f"**unattended since {time.strftime('%H:%M', time.localtime(started))} · interventions {interventions} · "
            f"version {version} · ${spent:.3f} spent**")

c1, c2 = st.columns(2)
with c1:
    st.subheader("Score and $/solved by run")
    if evals:
        df = pd.DataFrame([{k: e[k] for k in ("tag", "harness", "n", "solved", "acc", "cost_per_solved", "explore_per_task", "brain")} for e in evals])
        st.dataframe(df, use_container_width=True)
        st.line_chart(df[["acc", "explore_per_task"]])
with c2:
    st.subheader("Version family tree / archive")
    for a in archive[::-1][:30]:
        color = {"accepted": "green", "rejected": "red"}.get(a.get("status"), "gray")
        st.markdown(f":{color}[**{a.get('status')}**] gen {a.get('gen', '?')} · {a.get('file') or a.get('files')} — "
                    f"{a.get('hypothesis') or a.get('hypotheses') or ''} {('· ' + a['reason']) if a.get('reason') else ''}")
        if a.get("diff"):
            with st.expander("diff"):
                st.code(a["diff"], "diff")

c3, c4 = st.columns(2)
with c3:
    st.subheader("BRAIN.md (quarantined in red)")
    db = RUNS / "brain.db"
    if db.exists():
        for b in sqlite3.connect(db).execute("SELECT id,type,scope,text,helpful,harmful,trusted,quarantined,retired FROM bullets ORDER BY type,id"):
            tag = f"#{b[0]} [{b[1]}|{b[2]}] (+{b[4]}/-{b[5]}{', trusted' if b[6] else ''})"
            if b[7]:
                st.markdown(f":red[QUARANTINED {tag} {b[3]}]")
            elif b[8]:
                st.markdown(f"~~{tag} {b[3]}~~")
            else:
                st.markdown(f"{tag} {b[3]}")
with c4:
    st.subheader("Model mix (benchmark-routed) and escalations")
    ok = [r for r in routing if "role" in r and "error" not in r]
    if ok:
        df = pd.DataFrame(ok)
        st.dataframe(df.groupby(["role", "model"]).agg(calls=("cost", "size"), cost=("cost", "sum"), cached=("cached", "sum")).reset_index(),
                     use_container_width=True)
    st.caption(f"fallbacks: {sum(1 for r in routing if r.get('fallback'))} · provider errors: {sum(1 for r in routing if 'error' in r)}")
    stats = RUNS / "router_stats.json"
    if stats.exists():
        st.json(json.loads(stats.read_text()), expanded=False)
    st.subheader("Sandbox denial feed")
    for d in denials[::-1][:20]:
        st.markdown(f":red[{time.strftime('%H:%M:%S', time.localtime(d['ts']))} {d['kind']}] `{d['detail'][:120]}`")

st.subheader("Current routing policy (editable by the improver)")
st.code(yaml.safe_dump(yaml.safe_load((ROOT / "harness" / f"v{version}" / "routing.yaml").read_text()), sort_keys=False), "yaml")
