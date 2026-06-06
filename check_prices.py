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
import serpapi_probe
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
        msg = notify.deal_message(origin, dest, deal, currency=currency, source="Travelpayouts")
        if notify.send(chat_id, msg, token=tg_token):
            store.mark_alerted(origin, dest, deal["link"])
            alerts_sent += 1
            logger.info("Alert: %s->%s %.0f (-%0.f%%)",
                        origin, dest, deal["price"], deal["discount_pct"])

    return len(flights), alerts_sent


def send_heartbeat(tg_token: str, chat_id: str,
                   tp: dict, serp: dict):
    now = datetime.now(timezone.utc).strftime("%d %b %H:%M UTC")
    total_alerts = tp["alerts"] + serp.get("alerts", 0)
    status = "🚨" if total_alerts > 0 else ("✅" if tp["routes_with_data"] > 0 else "⚠️")

    lines = [
        f"{status} *Flight Bot — Hourly Check*",
        f"🕐 {now}",
        "",
        f"*Travelpayouts*",
        f"  📡 {tp['routes_with_data']}/{tp['routes_total']} routes with data",
        f"  ✈️ {tp['flights']:,} flights fetched",
        f"  🚨 {tp['alerts']} deal(s) sent",
    ]

    if serp.get("ran"):
        lines += [
            "",
            f"*Google Flights (SerpApi)*",
            f"  📡 {serp['with_data']}/{serp['probed']} routes probed",
            f"  ✈️ {serp['flights']:,} flights fetched",
            f"  🚨 {serp['alerts']} deal(s) sent",
            f"  💳 Budget left: {serp['budget_left']} calls this month",
        ]
    else:
        # Probe only runs once/day — report clearly WHY it didn't run this hour,
        # so the ~23 non-probe runs/day don't look like a failure.
        s = serp.get("status", "")
        left = serp.get("budget_left")
        ptime = serp.get("probe_time", "01:00")
        if s == "already_today":
            lines.append(f"\n_SerpApi: ✅ ran earlier today · {left} of monthly budget left_")
        elif s == "before_time":
            lines.append(f"\n_SerpApi: ⏳ today's probe scheduled for {ptime} IST_")
        elif s == "not_probe_day":
            lines.append("\n_SerpApi: 💤 not a probe day (Mon–Fri + alternate Sat)_")
        elif s == "no_key":
            lines.append("\n_SerpApi: ➕ add SERPAPI\\_KEY secret to enable_")
        else:
            lines.append("\n_SerpApi: idle_")

    if total_alerts == 0 and tp["routes_with_data"] > 0:
        lines.append("\n_Building price baseline — deals fire once history accumulates_")
    elif tp["routes_with_data"] == 0:
        lines.append("\n_⚠️ No data from Travelpayouts — check TRAVELPAYOUTS\\_TOKEN secret_")

    notify.send(chat_id, "\n".join(lines), token=tg_token)


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

    tp_stats = {"routes_total": len(routes), "routes_with_data": 0,
                "flights": 0, "alerts": 0, "errors": 0}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_route, api, route, tg_token): route for route in routes}
        for future in as_completed(futures):
            route = futures[future]
            try:
                flights, alerts = future.result()
                tp_stats["flights"] += flights
                tp_stats["alerts"] += alerts
                if flights > 0:
                    tp_stats["routes_with_data"] += 1
            except Exception as e:
                tp_stats["errors"] += 1
                logger.error("Error on %s->%s: %s", route["origin"], route["destination"], e)

    # SerpApi probe — round-trip prices for the watchlist, once per scheduled day
    serp_stats = serpapi_probe.run_probe(tg_token, chat_id)

    logger.info("TP: flights=%d alerts=%d routes_with_data=%d errors=%d",
                tp_stats["flights"], tp_stats["alerts"],
                tp_stats["routes_with_data"], tp_stats["errors"])

    send_heartbeat(tg_token, chat_id, tp_stats, serp_stats)


if __name__ == "__main__":
    main()
