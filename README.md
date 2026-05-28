# StockWatch

Your friendly stock watcher. 📈

Track stock prices, technical indicators, Reddit sentiment, and finance news from the command line.

## Installation

```bash
pip install -e ".[news]"   # includes RSS news digest
pip install -e ".[reddit]"  # includes Reddit sentiment
pip install -e ".[news,reddit]"  # everything
```

## Quick start

```bash
# Default watchlist (AMD, NVDA, GOOG, MSFT, AAPL, MU)
stockwatch

# RSS finance news digest (no auth required)
stockwatch --news

# Reddit sentiment analysis (requires Reddit API + OpenRouter)
export REDDIT_CLIENT_ID=your_client_id
export REDDIT_CLIENT_SECRET=***
export OPENROUTER_API_KEY=***
stockwatch --reddit

# All together
stockwatch --news --reddit
```

## Features

- **Yahoo Finance prices** via `yfinance` (real-time quotes, fundamentals)
- **Technical indicators**: SMA(20/50), RSI(14), MACD, Bollinger Bands
- **Trend tracking**: 1-day, 1-week, 1-month percent change
- **Reddit sentiment**: Searches r/wallstreetbets, r/stocks, r/investing, r/StockMarket
- **LLM summarization**: Uses OpenRouter to summarize Reddit buzz per ticker
- **RSS news digest**: Fetches from WSJ Markets, Yahoo Finance, Investing.com, CNBC Markets
- **Watch mode**: Continuous terminal dashboard with configurable refresh
- **TOML config**: `~/.stockwatch.toml` for persistent watchlist (not git-tracked)

## Configuration

Create `~/.stockwatch.toml` (git-ignored):

```toml
tickers = ["AMD", "NVDA", "GOOG", "MSFT", "AAPL", "MU"]
```

## Environment variables

| Variable | Required for | Description |
|---|---|---|
| `REDDIT_CLIENT_ID` | `--reddit` | Reddit app client ID |
| `REDDIT_CLIENT_SECRET` | `--reddit` | Reddit app client secret |
| `OPENROUTER_API_KEY` | `--reddit` | OpenRouter API key for LLM summarization |

## RSS feeds (no auth required)

Default feeds: WSJ Markets, Yahoo Finance, Investing.com, CNBC Markets.
Override with `--news-feeds url1,url2,...`

## Example output (with `--news --reddit`)

```
============================================================================================================
TICKER     PRICE     CHANGE     %CHG         TREND(1d/1w/1m)  RSI(14)   SMA(20)   SMA(50)      MACD    VOL(20d)
------------------------------------------------------------------------------------------------------------
   AMD   $518.09    +22.55  +455.06%  +4.55% / +15.24% / +46.15%     74.5   $432.02   $321.81   49.1331  40,318,669
  NVDA   $214.25     +1.65  +77.61%  +0.78% / -2.40% / +7.36%     52.5   $214.88   $198.73    4.6291  162,075,546
  GOOG   $386.12     +1.29  +33.52%  +0.34% / +0.69% / +1.09%     41.9   $387.91   $343.65   10.7281  18,959,975
  MSFT   $426.99    +14.32  +347.01%  +3.47% / +1.89% / +4.94%     55.5   $414.80   $400.88    3.8334  34,169,139
  AAPL   $312.51     +1.66  +53.40%  +0.53% / +2.47% / +15.27%     88.2   $295.41   $273.86   10.5026  49,882,644
    MU   $923.52     -4.89  -52.67%  -0.53% / +21.18% / +78.58%     72.7   $728.77   $547.35   94.6482  54,584,507
========================================================================================

📰  NEWS DIGEST (last 24h)
================================================================================

--- MU ---
  [08:03] Did Elon Musk Just Rig the Stock Market?
       https://www.reddit.com/r/videos/comments/1tpx09i/...

--- NVDA ---
  [14:22] Nvidia reports strong Q1 results, AI demand continues
       https://finance.yahoo.com/news/nvidia-q1-2026-results-ai-demand...

================================================================================
```
