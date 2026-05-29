# StockWatch — Discord Monitor

Polls a configurable watchlist every 30 minutes, computes technical indicators, pulls per-ticker news, feeds everything to Claude for a summary, and posts color-coded embeds to a private Discord channel.

**Personal use only. Educational output. Not investment advice.**

---

## Setup

### 1. Prerequisites (macOS)

```bash
brew install python@3.12 ta-lib   # ta-lib C library optional but recommended
```

### 2. Clone & virtual environment

```bash
cd ~/stockbot
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install TA-Lib   # optional; requires brew install ta-lib above
```

### 3. Accounts & API keys

| Service | Where to get key |
|---------|-----------------|
| Finnhub | [finnhub.io](https://finnhub.io) → free tier |
| Anthropic | [console.anthropic.com](https://console.anthropic.com) |
| Discord webhook | Channel Settings → Integrations → Webhooks → New Webhook |

### 4. Configure

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
# edit .env with your keys
```

Edit `config.yaml` to set your watchlist and preferences.

### 5. Test a single run

```bash
source .venv/bin/activate
python main.py
```

---

## Schedule with launchd (macOS)

```bash
mkdir -p ~/stockbot/logs

# Edit the plist — replace /Users/YOU with your actual home path
cp launchd/com.user.stockbot.plist ~/Library/LaunchAgents/
nano ~/Library/LaunchAgents/com.user.stockbot.plist

# Load and start
launchctl load ~/Library/LaunchAgents/com.user.stockbot.plist
launchctl start com.user.stockbot

# Verify
launchctl list | grep stockbot

# View logs
tail -f ~/stockbot/logs/out.log
```

To stop / unload:

```bash
launchctl unload ~/Library/LaunchAgents/com.user.stockbot.plist
```

---

## Project layout

```
config.yaml          watchlist + settings
.env                 secrets (gitignored)
main.py              orchestrator
sources/
  finnhub_src.py     real-time quotes + company news (Finnhub)
  yf_fallback.py     OHLC history + fallback quote (yfinance)
  news_rss.py        Yahoo Finance per-ticker RSS
indicators.py        pandas-ta computations + derive_signal()
llm.py               Claude Haiku call with prompt caching
discord_out.py       embed builder + webhook poster
store.py             SQLite: ohlc_cache, seen_news, last_signal
launchd/             macOS LaunchAgent plist
```

---

## Cost estimate (personal scale)

- **LLM:** Claude Haiku 4.5 at ~$1/$5 per Mtok; with the new-signal gate + prompt caching, typically **a few dollars/month**.
- **Finnhub:** free tier is 60 req/min — this script uses a tiny fraction.
- **Discord:** webhooks allow 5 req / 2s; batching ≤10 embeds/POST keeps you clear.

---

## Guardrails

- Every post carries **"Educational only — not investment advice."**
- Headlines + links only — no full article text.
- Keep the channel **private**.
- Free quotes are typically ~15-min delayed.
