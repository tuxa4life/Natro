"""Settings from .env and the word list."""
import os
from pathlib import Path

VOICE_DIR = Path(__file__).resolve().parent.parent
# .env and the personal data folders (logs, test set, results) are at the top of
# the repo, shared with the agent. NATRO_HOME overrides it.
ROOT = Path(os.environ.get("NATRO_HOME") or VOICE_DIR.parent)


def load_env():
    """Load .env into os.environ. Variables already set take precedence."""
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds and not Path(creds).is_absolute():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(ROOT / creds)


def load_wordlist():
    """Names, brands and terms from wordlist.txt, one per line."""
    path = VOICE_DIR / "wordlist.txt"
    if not path.exists():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]
