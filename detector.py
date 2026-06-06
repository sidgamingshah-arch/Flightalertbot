import statistics
import logging
import store

logger = logging.getLogger(__name__)

Z_SCORE_THRESHOLD = -1.5    # standard deviations below mean
PCT_DROP_THRESHOLD = 0.70   # price must be < 70% of rolling mean
MIN_HISTORY_POINTS = 8      # minimum data points before baseline is trusted


def find_deals(origin: str, destination: str, recent_flights: list[dict],
               *, min_history: int = MIN_HISTORY_POINTS, drop_pct: float | None = None,
               use_zscore: bool = True) -> list[dict]:
    """Detect deals against the rolling baseline.

    Defaults reproduce the original Travelpayouts behaviour (30% drop OR z<=-1.5,
    8+ points). Callers can override for a different source/strategy — e.g. the
    SerpApi round-trip probe uses a pure 15% baseline_drop with 3+ points.
    """
    # drop_pct (e.g. 15) -> price must be <= (1 - 0.15) = 0.85 of the mean.
    pct_threshold = (1 - drop_pct / 100) if drop_pct is not None else PCT_DROP_THRESHOLD

    history = store.get_history(origin, destination)

    if len(history) < min_history:
        logger.info("%s->%s: only %d history points (need %d), skipping detection",
                    origin, destination, len(history), min_history)
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
        is_deal = mean > 0 and price / mean <= pct_threshold
        if use_zscore and stdev > 0:
            is_deal = is_deal or z <= Z_SCORE_THRESHOLD

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
