import logging
import requests
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

BASE_URL = "https://api.travelpayouts.com/v1/prices/cheap"
BOOK_BASE = "https://www.aviasales.com"


class TravelpayoutsAPI:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({"X-Access-Token": token})

    def search(
        self,
        origin: str,
        destination: str,
        currency: str = "USD",
        lookahead_months: int = 3,
    ) -> list[dict]:
        results = []
        today = datetime.utcnow()

        for offset in range(lookahead_months + 1):
            month = (today + relativedelta(months=offset)).strftime("%Y-%m")
            batch = self._fetch_month(origin, destination, month, currency)
            results.extend(batch)

        # Deduplicate by booking link
        seen = set()
        unique = []
        for r in results:
            if r["link"] not in seen:
                seen.add(r["link"])
                unique.append(r)

        if unique:
            logger.info("Fetched %d fares for %s->%s", len(unique), origin, destination)
        else:
            logger.warning("NO DATA returned for %s->%s (currency=%s)", origin, destination, currency)

        return unique

    def _fetch_month(
        self, origin: str, destination: str, depart_date: str, currency: str
    ) -> list[dict]:
        params = {
            "origin": origin,
            "destination": destination,
            "depart_date": depart_date,
            "currency": currency,
            "limit": 30,
            "page": 1,
            "show_to_affiliates": "true",
            "sorting": "price",
        }
        try:
            resp = self.session.get(BASE_URL, params=params, timeout=15)
            resp.raise_for_status()
            body = resp.json()
        except requests.RequestException as e:
            logger.error("API error %s->%s %s: %s", origin, destination, depart_date, e)
            return []

        if not body.get("success"):
            logger.warning("success=false for %s->%s %s: %s", origin, destination, depart_date, body)
            return []

        raw_data = body.get("data") or {}

        # Travelpayouts keys data by destination code — try exact match first,
        # then fall back to the first key in the response (handles city-code variants)
        dest_data = raw_data.get(destination) or next(iter(raw_data.values()), {})

        if not dest_data:
            return []

        flights = []
        for _transfers, info in dest_data.items():
            try:
                link_path = info.get("link", "")
                flights.append({
                    "price": float(info["price"]),
                    "airline": info.get("airline", ""),
                    "departure_date": info.get("departure_at", "")[:10],
                    "link": BOOK_BASE + link_path if link_path.startswith("/") else link_path,
                    "duration_hrs": round(info.get("duration", 0) / 60, 1),
                    "stops": int(info.get("transfers", 0)),
                })
            except (KeyError, TypeError, ValueError):
                continue

        return flights
