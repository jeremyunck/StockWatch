# StockWatch — Discord Monitor

Polls a configurable watchlist, computes technical indicators, pulls per-ticker
news, and posts color-coded embeds to a private Discord channel. An LLM is asked
for a short plain-English read **only when the signal or the news actually
changes** — everything else in the post is computed deterministically.

**Personal use only. Educational output. Not investment advice.**

---

## How it works

Each run does one full cycle per ticker:

1. **Fetch data** — quote (Finnhub, yfinance fallback) + 1y of daily OHLC.
2. **Compute** — indicators (SMA/EMA/RSI/MACD/Bollinger/ATR/OBV) and a
   deterministic `LEAN_BUY / HOLD / LEAN_SELL` signal. This drives the embed
   layout and color.
3. **Fetch news** — Finnhub company news + Yahoo Finance RSS, deduped.
4. **Summarize (LLM)** — only if the signal changed or there is unseen news, the
   indicators + news are sent to the model for a 2–3 sentence summary and a
   recommendation. This is the **only** dynamic section of the post.
5. **Post** — one embed per ticker plus a summary line, batched to Discord.

The new-signal gate (`only_call_llm_on_new_signal`) keeps LLM usage — and cost —
to a minimum.

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
```

### 3. API keys

| Service | Where to get it |
|---------|-----------------|
| Finnhub | [finnhub.io](https://finnhub.io) → free tier (quotes + news) |
| OpenRouter | [openrouter.ai](https://openrouter.ai) → API key (runs Claude Haiku 4.5) |
| Discord webhook | Channel Settings → Integrations → Webhooks → New Webhook |

### 4. Configure

```bash
cp .env.example .env     # then edit .env with your keys
```

Edit `config.yaml` to set your watchlist, the model, and the gating behavior.

### 5. Run once

```bash
source .venv/bin/activate
python main.py
```

### 6. Run the tests (no network)

```bash
pytest -q
```

---

## Schedule with launchd (macOS)

```bash
mkdir -p ~/stockbot/logs
cp launchd/com.user.stockbot.plist ~/Library/LaunchAgents/
# Edit the /Users/YOU paths inside the plist to match your home directory
launchctl load ~/Library/LaunchAgents/com.user.stockbot.plist
launchctl start com.user.stockbot
tail -f ~/stockbot/logs/out.log     # view logs
```

To stop: `launchctl unload ~/Library/LaunchAgents/com.user.stockbot.plist`

---

## Project layout

```
config.yaml          watchlist + settings
.env                 secrets (gitignored)
main.py              orchestrator: fetch → compute → (LLM) → post
sources/
  finnhub_src.py     real-time quotes + company news (Finnhub)
  yf_fallback.py     OHLC history + fallback quote (yfinance)
  news_rss.py        Yahoo Finance per-ticker RSS
indicators.py        pandas-ta computations + derive_signal()
llm.py               OpenRouter call (Claude Haiku 4.5) + new-signal gate
discord_out.py       embed builder + webhook poster
store.py             SQLite: ohlc_cache, seen_news, last_signal
launchd/             macOS LaunchAgent plist
tests/               unit tests for signals, gate, and embeds
ml/                  optional, standalone XGBoost training pipeline (see ml/README.md)
```

---

## Cost estimate (personal scale)

- **LLM:** Claude Haiku 4.5 via OpenRouter. With the new-signal gate, most cycles
  make **zero** LLM calls, so typical spend is **a few dollars/month**.
- **Finnhub:** free tier is 60 req/min — this script uses a tiny fraction.
- **Discord:** webhooks allow 5 req / 2s; batching ≤10 embeds/POST keeps you clear.

---

## Guardrails

- Every post carries **"Educational only — not investment advice."**
- Headlines + links only — no full article text.
- Keep the channel **private**.
- Free quotes are typically ~15-min delayed.
</content>
