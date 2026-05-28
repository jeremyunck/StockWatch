# StockWatch

Your friendly stock watcher. 📈

Track stock prices, technical indicators, and Reddit sentiment from the command line.

## Installation

```bash
pip install -e ".[reddit]"
```

## Quick start

```bash
# Default watchlist (NVDA, AMD, MU)
stockwatch

# Custom tickers
stockwatch NVDA AMD MU

# With Reddit sentiment analysis (requires Reddit API + OpenRouter)
export REDDIT_CLIENT_ID=your_client_id
export REDDIT_CLIENT_SECRET=your_secret
export OPENROUTER_API_KEY=sk-or-...
stockwatch --reddit
```

## Features

- **Yahoo Finance prices** via `yfinance` (real-time quotes, fundamentals)
- **Technical indicators**: SMA(20/50), RSI(14), MACD, Bollinger Bands
- **Trend tracking**: 1-day, 1-week, 1-month percent change
- **Reddit sentiment**: Searches r/wallstreetbets, r/stocks, r/investing, r/StockMarket
- **LLM summarization**: Uses OpenRouter to summarize Reddit buzz per ticker
- **Watch mode**: Continuous terminal dashboard with configurable refresh
- **TOML config**: `~/.stockwatch.toml` for persistent watchlist

## Configuration

Create `~/.stockwatch.toml`:

```toml
tickers = ["NVDA", "AMD", "MU"]
```

## Example output (with `--reddit`)

```
==========================================================================================
TICKER     PRICE     %CHG  TREND(1d/1w/1m)  REDDIT↓24H    SENTIMENT (Reddit / LLM)
----------------------------------------------------------------------------------------
  NVDA   $214.16   +0.74%  +0.74% / -2.43% / +7.32%     3 posts   0.742  Bullish buzz on AI demand...
   AMD   $518.53   +4.66%  +4.66% / +15.35% / +46.3%    1 post    0.810  Mixed — strong week...
    MU   $925.32   -0.33%  -0.33% / +21.42% / +78.92%    0 posts   ---     ---
==========================================================================================
```

## Environment variables

| Variable | Required for | Description |
|---|---|---|
| `REDDIT_CLIENT_ID` | `--reddit` | Reddit app client ID |
| `REDDIT_CLIENT_SECRET` | `--reddit` | Reddit app client secret |
| `OPENROUTER_API_KEY` | `--reddit` | OpenRouter API key for LLM summarization |
