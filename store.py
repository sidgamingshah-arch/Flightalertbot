"""JSON-based price store that commits cleanly to git."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path("data")
PRICES_FILE = DATA_DIR / "prices.json"
ALERTS_FILE = DATA_DIR / "alerts_sent.json"


def _read(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def _write(path: Path, data: dict):
    DATA_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def record_prices(origin: str, destination: str, flights: list[dict]):
    key = f"{origin}-{destination}"
    store = _read(PRICES_FILE)
    history = store.get(key, [])

    now = datetime.utcnow().isoformat()
    for f in flights:
        history.append({
            "price": f["price"],
            "airline": f["airline"],
            "departure_date": f["departure_date"],
            "link": f["link"],
            "duration_hrs": f.get("duration_hrs", 0),
            "stops": f.get("stops", 0),
            "checked_at": now,
        })

    # Keep only last 30 days
    cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
    history = [h for h in history if h["checked_at"] >= cutoff]

    store[key] = history
    _write(PRICES_FILE, store)


def get_history(origin: str, destination: str) -> list[dict]:
    key = f"{origin}-{destination}"
    return _read(PRICES_FILE).get(key, [])


def already_alerted(origin: str, destination: str, link: str) -> bool:
    key = f"{origin}-{destination}"
    sent = _read(ALERTS_FILE).get(key, {})
    entry = sent.get(link)
    if not entry:
        return False
    # Re-alert after 12 hours so recurring flash sales aren't silenced forever
    return entry >= (datetime.utcnow() - timedelta(hours=12)).isoformat()


def mark_alerted(origin: str, destination: str, link: str):
    key = f"{origin}-{destination}"
    sent = _read(ALERTS_FILE)
    bucket = sent.get(key, {})
    bucket[link] = datetime.utcnow().isoformat()
    # Prune old entries
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    bucket = {k: v for k, v in bucket.items() if v >= cutoff}
    sent[key] = bucket
    _write(ALERTS_FILE, sent)
