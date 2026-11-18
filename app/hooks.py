"""Pick the on-screen hook with the master-virality skill's hook scorer (Jake Schincariol, MIT).

The AI writes a few hook lines per clip; the skill's hookscore.py scores each on length, specificity, stakes,
how early the payoff lands and who it speaks to, and the best one goes on screen. Without the skill installed,
the AI's first line is used.
"""

import json
import subprocess
import sys
from pathlib import Path

SCORER = Path.home() / ".claude" / "skills" / "master-virality" / "tools" / "hookscore.py"


def scores(hooks: list[str]) -> list[float] | None:
    """hookscore.py's 0-100 score per hook, or None when the skill is missing or fails."""
    if not SCORER.is_file():
        return None
    try:
        out = subprocess.run([sys.executable, "-I", str(SCORER), "-", "--json"], input="\n".join(hooks),
                             capture_output=True, text=True, timeout=20).stdout
        rows = json.loads(out)
        rows = rows if isinstance(rows, list) else [rows]
        return [float(r["score"]) for r in rows] if len(rows) == len(hooks) else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def best_hook(candidates: list[str]) -> str:
    """The highest-scoring hook; ties and a missing scorer keep the AI's order."""
    hooks = [" ".join(h.split()) for h in candidates if isinstance(h, str) and h.strip()]
    if len(hooks) < 2:
        return hooks[0] if hooks else ""
    marks = scores(hooks)
    return hooks[0] if marks is None else max(zip(hooks, marks), key=lambda hm: hm[1])[0]
