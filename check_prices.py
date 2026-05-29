"""Entry point — runs as a one-shot script inside GitHub Actions every 15 minutes."""
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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
MAX_WORKERS = 10  # concurrent route checks — stays within Travelpayouts rate limits


def load_routes() -> list[dict]:
    if not ROUTES_FILE.exists():
        logger.error("routes.json not found")
        sys.exit(1)
    return json.loads(ROUTES_FILE.read_text())


def check_route(api: TravelpayoutsAPI, route: dict, tg_token: str) -> int:
    origin = route["origin"].upper()
    dest = route["destination"].upper()
    chat_id = str(route["telegram_chat_id"])
    currency = route.get("currency", "USD")

    flights = api.search(origin, dest, currency=currency)
    if not flights:
        return 0

    store.record_prices(origin, dest, flights)
    deals = detector.find_deals(origin, dest, flights)
    alerts_sent = 0

    for deal in deals:
        msg = notify.deal_message(origin, dest, deal)
        if notify.send(chat_id, msg, token=tg_token):
            store.mark_alerted(origin, dest, deal["link"])
            alerts_sent += 1
            logger.info("Alert: %s->%s $%.0f (-%0.f%%)", origin, dest, deal["price"], deal["discount_pct"])

    return alerts_sent


def main():
    tp_token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")

    if not tp_token or not tg_token:
        logger.error("Missing TRAVELPAYOUTS_TOKEN or TELEGRAM_BOT_TOKEN env vars")
        sys.exit(1)

    api = TravelpayoutsAPI(tp_token)
    routes = load_routes()
    logger.info("Checking %d routes with %d workers", len(routes), MAX_WORKERS)

    total_alerts = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_route, api, route, tg_token): route for route in routes}
        for future in as_completed(futures):
            route = futures[future]
            try:
                total_alerts += future.result()
            except Exception as e:
                logger.error("Error on %s->%s: %s", route["origin"], route["destination"], e)

    logger.info("Done. %d deal alert(s) sent across %d routes.", total_alerts, len(routes))


if __name__ == "__main__":
    main()
