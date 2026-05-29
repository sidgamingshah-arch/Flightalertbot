import statistics
import logging
import store

logger = logging.getLogger(__name__)

Z_SCORE_THRESHOLD = -1.5    # standard deviations below mean
PCT_DROP_THRESHOLD = 0.70   # price must be < 70% of rolling mean
MIN_HISTORY_POINTS = 8      # minimum data points before baseline is trusted


def find_deals(origin: str, destination: str, recent_flights: list[dict]) -> list[dict]:
    history = store.get_history(origin, destination)

    if len(history) < MIN_HISTORY_POINTS:
        logger.info("%s->%s: only %d history points, skipping detection", origin, destination, len(history))
        return []

    prices = [h["price"] for h in history]
    mean = statistics.mean(prices)
    stdev = statistics.stdev(prices) if len(prices) > 1 else 0

    deals = []
    for flight in recent_flights:
        price = flight["price"]
        link = flight.get("link", "")
        if not link:
            continue
        if store.already_alerted(origin, destination, link):
            continue

        z = (price - mean) / stdev if stdev > 0 else 0
        is_deal = z <= Z_SCORE_THRESHOLD or (mean > 0 and price / mean <= PCT_DROP_THRESHOLD)

        if is_deal:
            discount_pct = round((1 - price / mean) * 100, 1)
            logger.info("DEAL %s->%s $%.0f (mean $%.0f, -%.0f%%, z=%.2f)",
                        origin, destination, price, mean, discount_pct, z)
            deals.append({
                **flight,
                "avg_price": round(mean, 2),
                "discount_pct": discount_pct,
                "z_score": round(z, 2),
            })

    return deals
