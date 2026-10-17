# Results (measured; updated by hand from runs/evals.jsonl, small samples, interpret with care)

| run | harness | tasks | solved | $ total | $/solved | exploration calls/task |
|---|---|---|---|---|---|---|
| g0-base-train | v0 | 8 | 4 | 0.0808 | 0.02021 | 8.25 |

Model mix so far (calls by role, chosen by the benchmark router):

| role | model | calls |
|---|---|---|
| analyst | Deepseek-V4-Flash | 81 |
| analyst | NVIDIA-Nemotron-3-Nano-30B-A3B | 18 |
| improver | Deepseek-V4-Flash | 1 |
| lead | Deepseek-V4-Flash | 81 |
| reflector | NVIDIA-Nemotron-3-Nano-30B-A3B | 8 |
| scout | Deepseek-V4-Flash | 82 |
| scout | NVIDIA-Nemotron-3-Nano-30B-A3B | 62 |
| verifier | Deepseek-V4-Flash | 23 |
| verifier | NVIDIA-Nemotron-3-Nano-30B-A3B | 3 |
| verifier | NVIDIA-Nemotron-3-Super-120B-A12B | 16 |
| verifier | Nemotron-3.5-Lightning-30B-A3B | 72 |

Total spend so far: $0.169; prompt tokens served from Crusoe cache: 586,368.

Pending (run `python -m groundhog.evolve table` after evolution): 2×2 on 12 held-out test tasks, DB-B transfer (12 tasks).
