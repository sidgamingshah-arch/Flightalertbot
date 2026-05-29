"""Send Telegram messages — plain HTTP, no library needed."""
import logging
import os
import requests

logger = logging.getLogger(__name__)
TG_URL = "https://api.telegram.org/bot{token}/sendMessage"

CURRENCY_SYMBOLS = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "AUD": "A$",
    "CAD": "C$",
    "SGD": "S$",
    "AED": "AED ",
}


def _symbol(currency: str) -> str:
    return CURRENCY_SYMBOLS.get(currency.upper(), currency + " ")


def send(chat_id: str, text: str, token: str | None = None) -> bool:
    token = token or os.environ["TELEGRAM_BOT_TOKEN"]
    try:
        resp = requests.post(
            TG_URL.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": False},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error("Telegram send failed to %s: %s", chat_id, e)
        return False


def deal_message(origin: str, destination: str, deal: dict, currency: str = "INR") -> str:
    sym = _symbol(currency)
    stops_label = "Non-stop" if deal["stops"] == 0 else f"{deal['stops']} stop(s)"
    return (
        f"🚨 *FLIGHT DEAL — {origin} → {destination}*\n\n"
        f"💰 *{sym}{deal['price']:,.0f}* _(30d avg: {sym}{deal['avg_price']:,.0f})_\n"
        f"📉 *{deal['discount_pct']:.0f}% below average* · Z\\-score: {deal['z_score']}\n"
        f"📅 Departs: {deal['departure_date']}\n"
        f"🏷️ {deal['airline']} · {stops_label} · {deal['duration_hrs']}h\n\n"
        f"[Book Now]({deal['link']})"
    )
