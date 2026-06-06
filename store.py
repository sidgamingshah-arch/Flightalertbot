"""JSON-based price store that commits cleanly to git.

NOTE: record_prices / mark_alerted are called from many worker threads at once
(check_prices.py runs routes concurrently). All file access is therefore guarded
by a single process-wide lock, and writes are atomic (temp file + os.replace), so
concurrent read-modify-write cycles can't clobber each other or wipe history via a
partial-read JSONDecodeError.
"""
import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path("data")
PRICES_FILE = DATA_DIR / "prices.json"
ALERTS_FILE = DATA_DIR / "alerts_sent.json"

# Cap history per route so prices.json (committed to git hourly) stays small.
# 200 points ≈ 8 days of hourly samples — ample for a rolling baseline.
MAX_HISTORY_PER_ROUTE = 200

_LOCK = threading.RLock()


def _read(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def _write(path: Path, data: dict):
    DATA_DIR.mkdir(exist_ok=True)
    # Atomic write: serialise to a temp file, then replace — a reader never sees
    # a half-written file.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    os.replace(tmp, path)


def record_prices(origin: str, destination: str, flights: list[dict]):
    """Record one baseline point (the cheapest fare this run) for the route.

    Storing only the cheapest fare per run keeps the baseline meaningful (it tracks
    the best available price over time) and keeps prices.json small even though each
    run fetches many fares across several date horizons.
    """
    if not flights:
        return
    key = f"{origin}-{destination}"
    cheapest = min(flights, key=lambda f: f["price"])
    with _LOCK:
        store = _read(PRICES_FILE)
        history = store.get(key, [])

        history.append({
            "price": cheapest["price"],
            "airline": cheapest["airline"],
            "departure_date": cheapest["departure_date"],
            "link": cheapest["link"],
            "duration_hrs": cheapest.get("duration_hrs", 0),
            "stops": cheapest.get("stops", 0),
            "checked_at": datetime.utcnow().isoformat(),
        })

        # Retain last 30 days, capped to MAX_HISTORY_PER_ROUTE most-recent points.
        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
        history = [h for h in history if h["checked_at"] >= cutoff][-MAX_HISTORY_PER_ROUTE:]

        store[key] = history
        _write(PRICES_FILE, store)


def get_history(origin: str, destination: str) -> list[dict]:
    key = f"{origin}-{destination}"
    with _LOCK:
        return _read(PRICES_FILE).get(key, [])


def already_alerted(origin: str, destination: str, link: str) -> bool:
    key = f"{origin}-{destination}"
    with _LOCK:
        sent = _read(ALERTS_FILE).get(key, {})
    entry = sent.get(link)
    if not entry:
        return False
    # Re-alert after 12 hours so recurring flash sales aren't silenced forever
    return entry >= (datetime.utcnow() - timedelta(hours=12)).isoformat()


def mark_alerted(origin: str, destination: str, link: str):
    key = f"{origin}-{destination}"
    with _LOCK:
        sent = _read(ALERTS_FILE)
        bucket = sent.get(key, {})
        bucket[link] = datetime.utcnow().isoformat()
        # Prune old entries
        cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        bucket = {k: v for k, v in bucket.items() if v >= cutoff}
        sent[key] = bucket
        _write(ALERTS_FILE, sent)
