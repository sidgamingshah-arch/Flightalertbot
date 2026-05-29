"""Entry point — runs as a one-shot script inside GitHub Actions every hour."""
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import store
import detector
import notify
from fetcher import TravelpayoutsAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

ROUTES_FILE = Path("routes.json")
MAX_WORKERS = 15


def load_routes() -> list[dict]:
    if not ROUTES_FILE.exists():
        logger.error("routes.json not found")
        sys.exit(1)
    return json.loads(ROUTES_FILE.read_text())


def check_route(api: TravelpayoutsAPI, route: dict, tg_token: str) -> tuple[int, int]:
    """Returns (flights_found, alerts_sent)."""
    origin = route["origin"].upper()
    dest = route["destination"].upper()
    chat_id = str(route["telegram_chat_id"])
    currency = route.get("currency", "USD")

    flights = api.search(origin, dest, currency=currency)
    if not flights:
        return 0, 0

    store.record_prices(origin, dest, flights)
    deals = detector.find_deals(origin, dest, flights)
    alerts_sent = 0

    for deal in deals:
        msg = notify.deal_message(origin, dest, deal, currency=currency)
        if notify.send(chat_id, msg, token=tg_token):
            store.mark_alerted(origin, dest, deal["link"])
            alerts_sent += 1
            logger.info("Alert sent: %s->%s %.0f (-%0.f%%)",
                        origin, dest, deal["price"], deal["discount_pct"])

    return len(flights), alerts_sent


def send_heartbeat(tg_token: str, chat_id: str, routes_total: int,
                   routes_with_data: int, flights_found: int,
                   alerts_sent: int, errors: int):
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    status = "✅" if routes_with_data > 0 else "⚠️"
    msg = (
        f"{status} *Flight Bot — Hourly Check*\n"
        f"🕐 {now}\n\n"
        f"📍 Routes checked: {routes_total}\n"
        f"📡 Routes with data: {routes_with_data}/{routes_total}\n"
        f"✈️ Flights found: {flights_found:,}\n"
        f"🚨 Deals sent: {alerts_sent}\n"
        f"❌ Errors: {errors}\n\n"
        f"_{'Building baseline — alerts fire once enough history accumulates.' if alerts_sent == 0 and routes_with_data > 0 else 'All good!' if alerts_sent > 0 else 'API returned no data — check token.'}_"
    )
    notify.send(chat_id, msg, token=tg_token)


def main():
    tp_token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")

    if not tp_token or not tg_token:
        logger.error("Missing TRAVELPAYOUTS_TOKEN or TELEGRAM_BOT_TOKEN")
        sys.exit(1)

    api = TravelpayoutsAPI(tp_token)
    routes = load_routes()
    chat_id = str(routes[0]["telegram_chat_id"])

    logger.info("Checking %d routes with %d workers", len(routes), MAX_WORKERS)

    total_flights = 0
    total_alerts = 0
    routes_with_data = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_route, api, route, tg_token): route for route in routes}
        for future in as_completed(futures):
            route = futures[future]
            try:
                flights, alerts = future.result()
                total_flights += flights
                total_alerts += alerts
                if flights > 0:
                    routes_with_data += 1
            except Exception as e:
                errors += 1
                logger.error("Error on %s->%s: %s", route["origin"], route["destination"], e)

    logger.info("Done. flights=%d alerts=%d routes_with_data=%d errors=%d",
                total_flights, total_alerts, routes_with_data, errors)

    send_heartbeat(tg_token, chat_id, len(routes), routes_with_data,
                   total_flights, total_alerts, errors)


if __name__ == "__main__":
    main()
