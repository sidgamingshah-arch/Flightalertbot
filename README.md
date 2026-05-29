# Flight Alert Bot

Checks flight prices every 15 minutes via GitHub Actions and sends Telegram alerts when deals or mispriced fares are detected. Completely free — no server required.

## How it works

1. GitHub Actions runs `check_prices.py` on a 15-minute cron
2. Script fetches prices from [Kiwi Tequila API](https://tequila.kiwi.com/portal/login)
3. Detects deals using Z-score anomaly detection against a 30-day rolling baseline
4. Sends Telegram message when a deal is found
5. Commits updated price history back to the repo

## Setup (one-time, ~10 minutes)

### 1. Get a Travelpayouts token (free, instant)
- Sign up at https://www.travelpayouts.com/developers/api
- Your API token is shown immediately on the dashboard — no approval needed

### 2. Create a Telegram bot
- Open Telegram, search for `@BotFather`
- Send `/newbot`, follow prompts, copy the **bot token**

### 3. Get your Telegram Chat ID
- Send any message to your new bot
- Open this URL in a browser (replace YOUR_TOKEN):
  `https://api.telegram.org/botYOUR_TOKEN/getUpdates`
- Find `"chat":{"id":XXXXXXXXX}` — that number is your chat ID

### 4. Fork this repo on GitHub
- Fork to your own GitHub account
- Make sure it is **public** (free unlimited Actions minutes)

### 5. Add GitHub Secrets
In your forked repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret name            | Value                         |
|------------------------|-------------------------------|
| `TRAVELPAYOUTS_TOKEN`  | your Travelpayouts API token  |
| `TELEGRAM_BOT_TOKEN`   | your Telegram bot token       |

### 6. Configure your routes
Edit `routes.json` and replace the values:

```json
[
  {
    "origin": "BOM",
    "destination": "LHR",
    "telegram_chat_id": "123456789",
    "currency": "USD"
  }
]
```

Use [IATA airport codes](https://www.iata.org/en/publications/directories/code-search/).

### 7. Enable Actions
- Go to the Actions tab in your repo
- Click "I understand my workflows, go ahead and enable them"
- Optionally click "Run workflow" to test immediately

## Deal detection logic

A flight is flagged as a deal when:
- Price is **≥ 1.5 standard deviations below** the 30-day mean (statistical anomaly), **OR**
- Price is **≥ 30% cheaper** than the 30-day average

The bot needs at least **8 price samples** (~2 hours) before it starts alerting — this prevents false positives on cold start.

## Notes

- GitHub may delay scheduled workflows by a few minutes during high load — this is normal
- GitHub disables scheduled workflows on repos with **no activity for 60 days** — push a small commit to re-enable
- Price history is stored in `data/prices.json` (committed automatically)
- Each deal link is suppressed for 12 hours to avoid repeat alerts on the same fare
