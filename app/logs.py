import json
from datetime import datetime, timezone


def log(step: str, outcome: str, event: str = "", delivery_id: str = "", repo: str = "", **extra):
    """One JSON line per log entry. Callers pass only safe values: never tokens, URLs or payloads."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "step": step,
        "event": event,
        "delivery_id": delivery_id,
        "repo": repo,
        "outcome": outcome,
        **extra,
    }
    print(json.dumps(entry), flush=True)
