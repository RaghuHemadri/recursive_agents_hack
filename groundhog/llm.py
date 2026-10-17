"""Crusoe client: retries 429/503 with exponential backoff + jitter, per-model concurrency limits, cost accounting,
and a Switchyard-style routing log (runs/routing.jsonl)."""
import json
import os
import random
import threading
import time
from pathlib import Path

from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError

ROOT = Path(__file__).resolve().parent.parent
from .config import RUNS  # noqa: E402
ROUTING_LOG = RUNS / "routing.jsonl"

# $ per 1M tokens (input, output), Crusoe serverless
PRICES = {
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.05, 0.20),
    "nvidia/Nemotron-3.5-Lightning-30B-A3B": (0.05, 0.20),
    "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B": (0.30, 2.40),
    "nvidia/NVIDIA-Nemotron-3.5-Super-VL": (0.30, 2.40),  # assumed = Super
    "nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B": (0.30, 1.83),
    "deepseek-ai/Deepseek-V4-Flash": (0.14, 0.28),
    "deepseek-ai/DeepSeek-V4-Pro": (1.74, 3.48),
    "openai/gpt-oss-120b": (0.05, 0.20),
    "zai-org/GLM-5.3": (1.40, 4.40),
    "Qwen/Qwen3.8-27B": (0.10, 0.40),
}


def load_env() -> None:
    for p in (ROOT / ".env", Path("/root/hh_dev/.env")):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


class Unavailable(Exception):
    """Model kept returning 429/503 after all retries; the router should fall back."""


class LLM:
    _sems: dict[str, threading.Semaphore] = {}
    _lock = threading.Lock()

    def __init__(self, per_model_concurrency: int = 16, max_attempts: int = 4):
        load_env()
        self.client = OpenAI(base_url=os.environ.get("LLM_BASE_URL", "https://api.inference.crusoecloud.com/v1"),
                             api_key=os.environ.get("LLM_API_KEY", "missing"), timeout=120, max_retries=0)
        self.conc, self.max_attempts = per_model_concurrency, max_attempts

    def _sem(self, model):
        with self._lock:
            return self._sems.setdefault(model, threading.Semaphore(self.conc))

    def chat(self, model: str, messages: list, tools: list | None = None, max_tokens: int = 4096, meta: dict | None = None):
        """Returns (message, usage_dict). Raises Unavailable after retries on 429/503/timeouts."""
        last = None
        for attempt in range(self.max_attempts):
            try:
                with self._sem(model):
                    t0 = time.time()
                    r = self.client.chat.completions.create(model=model, messages=messages, tools=tools or None,
                                                            max_tokens=max_tokens, temperature=0.2)
                u = r.usage
                cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
                pi, po = PRICES.get(model, (0.30, 2.40))
                usage = dict(model=model, tin=u.prompt_tokens, tout=u.completion_tokens, cached=cached,
                             cost=(u.prompt_tokens * pi + u.completion_tokens * po) / 1e6, latency=round(time.time() - t0, 2))
                self._log({**usage, **(meta or {}), "ts": time.time()})
                return r.choices[0].message, usage
            except (APIStatusError, APITimeoutError, APIConnectionError) as e:
                code = getattr(e, "status_code", None)
                if code is not None and code not in (408, 409, 429, 500, 502, 503, 504):
                    raise
                last = e
                time.sleep(min(20, 1.5 * 2 ** attempt) * random.uniform(0.5, 1.5))  # backoff + jitter
        self._log({"model": model, "error": str(last)[:120], **(meta or {}), "ts": time.time()})
        raise Unavailable(f"{model}: {last}")

    @staticmethod
    def _log(rec: dict) -> None:
        ROUTING_LOG.parent.mkdir(exist_ok=True)
        with LLM._lock, ROUTING_LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")
