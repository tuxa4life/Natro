"""The agent's log: every request and every tool call, one JSON line each, in logs/.

The cost of each request is logged too; this month's spending is summed from it.
"""
import json
from datetime import datetime

from natro_agent.config import LOG_DIR


def write(kind, **fields):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "kind": kind, **fields}
    with open(LOG_DIR / f"agent-{datetime.now():%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def entries(month):
    """Every logged entry of a month ("2026-10")."""
    for path in sorted(LOG_DIR.glob(f"agent-{month}-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)
