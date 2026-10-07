"""Where the PC app keeps its files, and its settings from .env."""
import os
from pathlib import Path

PC_DIR = Path(__file__).resolve().parent.parent
# .env, models/ and testset/ are at the top of the repo; NATRO_HOME overrides it.
ROOT = Path(os.environ.get("NATRO_HOME") or PC_DIR.parent)


def load_env():
    """Load .env into os.environ. Variables already set take precedence.

    The PC needs only NATRO_SERVER (Natro's address) and NATRO_DEVICE_TOKEN.
    """
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
