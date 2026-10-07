"""Paths and settings for the agent."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

AGENT_DIR = Path(__file__).resolve().parent.parent
# .env and the personal data folders (memory, notes, logs) are at the top of the
# repo; NATRO_HOME overrides it.
ROOT = Path(os.environ.get("NATRO_HOME") or AGENT_DIR.parent)
IDENTITY_FILE = AGENT_DIR / "identity" / "NATRO.md"
MCP_CONFIG = AGENT_DIR / "config" / "mcp.json"
LOG_DIR = ROOT / "logs"


def load_env():
    """Load .env into os.environ. Variables already set take precedence."""
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def timezone():
    """The owner's time zone (the VPS clock may be in another one)."""
    return ZoneInfo(os.environ.get("NATRO_TIMEZONE", "Asia/Tbilisi"))


def monthly_budget():
    """USD per month for Claude and Google together (voice + agent)."""
    return float(os.environ.get("NATRO_MONTHLY_BUDGET", "15"))
