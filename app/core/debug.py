import json, os, uuid, time
from datetime import datetime, timezone

DEBUG_AUTH = os.getenv("DEBUG_AUTH", "0").lower() in {"1","true","yes","on"}

def rid() -> str:
    return uuid.uuid4().hex[:12]

def log(event: str, **fields):
    if not DEBUG_AUTH:
        return
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    print("[DEBUG]", json.dumps(payload, default=str))
