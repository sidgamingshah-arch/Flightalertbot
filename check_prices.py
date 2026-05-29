"""Entry point — runs as a one-shot script inside GitHub Actions every 15 minutes."""
import json
import logging
import os
import sys
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


def load_routes() -> list[dict]:
    if not ROUTES_FILE.exists():
        logger.error("routes.json not found")
        sys.exit(1)
    return json.loads(ROUTES_FILE.read_text())


def main():
    tp_token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")

    if not tp_token or not tg_token:
        logger.error("Missing TRAVELPAYOUTS_TOKEN or TELEGRAM_BOT_TOKEN env vars")
        sys.exit(1)

    api = TravelpayoutsAPI(tp_token)
    routes = load_routes()
    total_alerts = 0

    for route in routes:
        origin = route["origin"].upper()
        dest = route["destination"].upper()
        chat_id = str(route["telegram_chat_id"])
        currency = route.get("currency", "USD")

        logger.info("Checking %s -> %s", origin, dest)

        flights = api.search(origin, dest, currency=currency)
        if not flights:
            logger.warning("No flights returned for %s->%s", origin, dest)
            continue

        store.record_prices(origin, dest, flights)

        deals = detector.find_deals(origin, dest, flights)

        for deal in deals:
            msg = notify.deal_message(origin, dest, deal)
            if notify.send(chat_id, msg, token=tg_token):
                store.mark_alerted(origin, dest, deal["link"])
                total_alerts += 1
                logger.info("Alert sent to %s for %s->%s at $%.0f", chat_id, origin, dest, deal["price"])

    logger.info("Done. %d alert(s) sent across %d route(s).", total_alerts, len(routes))


if __name__ == "__main__":
    main()
