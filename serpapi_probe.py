"""SerpApi Google Flights probe — secondary real-time price source.

Runs inside the same hourly GitHub Actions job after the Travelpayouts loop.
Only probes routes Travelpayouts returned NO data for, preserving the free quota.
Results are normalised into the same flight-dict shape as TravelpayoutsAPI.search()
so store / detector / notify are reused unchanged.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import store
import detector
import notify

logger = logging.getLogger(__name__)

WATCHLIST_FILE = Path("watchlist.json")
STATE_FILE = Path("data/serpapi_state.json")
SERPAPI_URL = "https://serpapi.com/search.json"
REQUEST_TIMEOUT = 30
_DAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


# ---------- config / state ----------

def _load_watchlist() -> dict | None:
    if not WATCHLIST_FILE.exists():
        logger.warning("watchlist.json not found — skipping SerpApi probe")
        return None
    return json.loads(WATCHLIST_FILE.read_text())


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"month": "", "calls_used": 0, "last_probe_date": ""}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _ensure_month(state: dict, now_local: datetime) -> dict:
    month_key = now_local.strftime("%Y-%m")
    if state.get("month") != month_key:
        state["month"] = month_key
        state["calls_used"] = 0
    return state


# ---------- schedule gate ----------

def _is_probe_day(now_local: datetime, schedule: dict) -> bool:
    day = _DAY_NAMES[now_local.weekday()]
    if day in schedule.get("probe_days", []):
        return True
    if day == "SAT":
        rule = schedule.get("saturday", "skip")
        if rule == "alternate_even_week":
            return now_local.isocalendar().week % 2 == 0
        return rule == "every"
    return False


def _past_probe_time(now_local: datetime, schedule: dict) -> bool:
    hh, mm = map(int, schedule.get("probe_local_time", "00:00").split(":"))
    return now_local >= now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)


def _eligible(now_local: datetime, schedule: dict, state: dict) -> bool:
    if not _is_probe_day(now_local, schedule):
        return False
    if not _past_probe_time(now_local, schedule):
        return False
    if state.get("last_probe_date") == now_local.date().isoformat():
        return False  # already probed today
    return True


# ---------- SerpApi query + normalisation ----------

def _query_serpapi(api_key: str, origin: str, dest: str,
                   trip: dict, currency: str, now_local: datetime) -> list[dict]:
    depart = now_local.date() + timedelta(days=trip.get("depart_offset_days", 60))
    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": dest,
        "outbound_date": depart.isoformat(),
        "currency": currency,
        "hl": "en",
        "api_key": api_key,
    }
    trip_type = trip.get("type", "one_way")
    if trip_type == "round_trip":
        params["type"] = 1
        params["return_date"] = (
            depart + timedelta(days=trip.get("return_offset_days", 7))
        ).isoformat()
    else:
        params["type"] = 2  # one-way

    resp = requests.get(SERPAPI_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return _normalise(resp.json(), origin, dest, currency)


def _normalise(data: dict, origin: str, dest: str, currency: str) -> list[dict]:
    """Map SerpApi google_flights response into the same shape as TravelpayoutsAPI.search().

    Required keys (see store.record_prices): price, airline, departure_date, link,
    duration_hrs, stops.
    """
    options = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    fallback_link = (
        (data.get("search_metadata") or {}).get("google_flights_url")
        or f"https://www.google.com/travel/flights?q=Flights+{origin}+to+{dest}"
    )

    flights = []
    for opt in options:
        price = opt.get("price")
        if price is None:
            continue

        segs = opt.get("flights") or []
        first_seg = segs[0] if segs else {}

        # departure_date: "YYYY-MM-DD" from "YYYY-MM-DD HH:MM"
        raw_dep = (first_seg.get("departure_airport") or {}).get("time", "")
        departure_date = raw_dep[:10] if raw_dep else ""

        # duration_hrs from total_duration (minutes)
        total_minutes = opt.get("total_duration") or 0
        duration_hrs = round(total_minutes / 60, 1)

        # stops = segments - 1
        stops = max(0, len(segs) - 1)

        flights.append({
            "price": float(price),
            "airline": first_seg.get("airline", ""),
            "departure_date": departure_date,
            "link": fallback_link,
            "duration_hrs": duration_hrs,
            "stops": stops,
        })

    return flights


# ---------- entry point ----------

def run_probe(tp_data_routes: set[tuple[str, str]], tg_token: str,
              chat_id: str, now_utc: datetime | None = None) -> dict:
    """Probe watchlist routes via SerpApi.

    tp_data_routes: {(ORIGIN, DEST)} that Travelpayouts already priced — skipped
    to avoid wasting quota on routes already covered for free.

    Returns stats dict for the heartbeat message.
    """
    stats = {"ran": False, "status": "unknown", "probe_time": "", "probed": 0,
             "with_data": 0, "flights": 0, "alerts": 0, "errors": 0, "budget_left": None}

    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        stats["status"] = "no_key"
        logger.info("SERPAPI_KEY not set — skipping SerpApi probe")
        return stats

    wl = _load_watchlist()
    if not wl:
        stats["status"] = "no_watchlist"
        return stats

    cfg = wl.get("serpapi", {})
    schedule = cfg.get("schedule", {})
    tz = ZoneInfo(schedule.get("timezone", "Asia/Kolkata"))
    now_utc = now_utc or datetime.now(tz=ZoneInfo("UTC"))
    now_local = now_utc.astimezone(tz)

    state = _ensure_month(_load_state(), now_local)
    budget = cfg.get("monthly_budget", 90)
    stats["probe_time"] = schedule.get("probe_local_time", "00:00")
    stats["budget_left"] = budget - state.get("calls_used", 0)

    # Explicit eligibility checks, each recording a reason for the heartbeat.
    if not _is_probe_day(now_local, schedule):
        stats["status"] = "not_probe_day"
        logger.info("SerpApi: not a probe day (%s)", now_local.strftime("%a"))
        return stats
    if state.get("last_probe_date") == now_local.date().isoformat():
        stats["status"] = "already_today"
        logger.info("SerpApi: already probed today (%d calls used)", state.get("calls_used", 0))
        return stats
    if not _past_probe_time(now_local, schedule):
        stats["status"] = "before_time"
        logger.info("SerpApi: before probe time %s (now %s)",
                    stats["probe_time"], now_local.strftime("%H:%M"))
        return stats

    trip = cfg.get("trip", {})
    currency = cfg.get("currency", "INR")
    stats["ran"] = True
    stats["status"] = "ran"

    # Priority 1 routes first — protects them when budget runs low
    routes = sorted(wl.get("routes", []), key=lambda r: r.get("priority", 2))

    for r in routes:
        origin, dest = r["from"].upper(), r["to"].upper()
        if (origin, dest) in tp_data_routes:
            logger.info("Skipping %s->%s — already priced by Travelpayouts", origin, dest)
            continue
        if state["calls_used"] >= budget:
            logger.info("SerpApi monthly budget (%d) reached", budget)
            break

        stats["probed"] += 1
        state["calls_used"] += 1
        try:
            flights = _query_serpapi(api_key, origin, dest, trip, currency, now_local)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code == 429:
                # Real SerpApi quota hit — stop cleanly regardless of our soft budget.
                stats["status"] = "quota_exhausted"
                stats["probed"] -= 1  # this call was rejected, not a real probe
                state["calls_used"] -= 1
                logger.warning("SerpApi quota exhausted (HTTP 429) — stopping probe")
                break
            stats["errors"] += 1
            logger.error("SerpApi HTTP %s %s->%s: %s", code, origin, dest, e)
            continue
        except Exception as e:
            stats["errors"] += 1
            logger.error("SerpApi error %s->%s: %s", origin, dest, e)
            continue

        if not flights:
            continue
        stats["with_data"] += 1
        stats["flights"] += len(flights)

        store.record_prices(origin, dest, flights)
        for deal in detector.find_deals(origin, dest, flights):
            msg = notify.deal_message(origin, dest, deal, currency=currency, source="Google Flights")
            if notify.send(chat_id, msg, token=tg_token):
                store.mark_alerted(origin, dest, deal["link"])
                stats["alerts"] += 1
                logger.info("SerpApi alert: %s->%s %.0f %s", origin, dest, deal["price"], currency)

    state["last_probe_date"] = now_local.date().isoformat()
    _save_state(state)
    stats["budget_left"] = budget - state["calls_used"]
    logger.info("SerpApi probe done: %s", stats)
    return stats
