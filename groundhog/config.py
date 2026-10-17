"""Which task world this process runs, and where its runs/ and harness/ live. GROUNDHOG_WORLD=gpuco (default) | azure."""
import importlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORLD = os.environ.get("GROUNDHOG_WORLD", "gpuco")
RUNS = ROOT / ("runs" if WORLD == "gpuco" else f"runs_{WORLD}")
HARNESS = ROOT / ("harness" if WORLD == "gpuco" else f"harness_{WORLD}")


def world():
    """The task world module (tasks, grading, tools, security tasks)."""
    return importlib.import_module("groundhog.world" if WORLD == "gpuco" else f"groundhog.world_{WORLD}")
