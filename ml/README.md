# Stock Buy-Signal Training Pipeline

> **Not investment advice.** This system identifies historical patterns that
> *would have* met a mechanical entry criterion (+5% in 5 trading days). Backtested
> edge routinely disappears in live trading. Use for research and learning only.

A self-contained machine-learning pipeline that:
1. Collects daily OHLC bars + 30-minute intraday snapshots into SQLite
2. Retroactively labels each snapshot (did the stock hit +5% within 5 trading days?)
3. Trains an XGBoost classifier on labeled observations with walk-forward validation

All files live in the `ml/` directory and are **fully independent** of the existing
StockWatch polling system — they share no code and use their own database file.

---

## Quick Start

```bash
cd StockWatch/ml
pip install -r requirements.txt
cp .env.example .env           # add your FINNHUB_API_KEY if you have one

# 0. Smoke-test with synthetic data (no network required)
python seed_test.py --db /tmp/test.db

# 1. One-time: backfill ~1 year of daily price history
python collect.py --db state.db --config config.yaml --init

# 2. Ongoing: run every 30 min during market hours (or manually)
python collect.py --db state.db --config config.yaml

# 3. Daily: resolve labels for matured snapshots (run after market close)
python label_job.py --db state.db

# 4. When enough labeled rows have accrued (default min: 200)
python train.py --db state.db --out-dir models
```

---

## Run Order

```
  ┌─────────────────────────────────────────────────────────────────┐
  │ 1. collect.py --init      (once, bootstraps price_history)      │
  │                                                                  │
  │ 2. collect.py             (every 30 min, market hours)           │
  │    → writes price_history + snapshots                            │
  │                                                                  │
  │ 3. label_job.py           (daily, after market close)            │
  │    → reads price_history, resolves labels on matured snapshots   │
  │    → snapshots <5 trading days old stay label=NULL               │
  │                                                                  │
  │ 4. train.py               (manual/weekly, once ≥200 labels)      │
  │    → builds features, walk-forward validates, saves model .json  │
  └─────────────────────────────────────────────────────────────────┘
```

**Label rule (v1):** `label = 1` if the intraday **high** reaches
`entry_price × 1.05` within the next **5 trading days**, else `0`.
Labels are *forward-looking* — they cannot be filled in until the window elapses.

---

## Files

| File | Purpose |
|---|---|
| `schema.sql` | SQLite schema (price_history, snapshots, labels) |
| `collect.py` | Fetches OHLCV from yfinance/Finnhub, computes indicators via pandas-ta, writes snapshots |
| `label_job.py` | Resolves buy/no-buy labels retroactively once the 5-day window matures |
| `train.py` | Builds features, walk-forward validates, trains XGBoost, saves versioned model |
| `seed_test.py` | Synthetic smoke test — no network required, verifies hit/miss/pending logic |
| `config.yaml` | Watchlist tickers |
| `.env.example` | API key template |
| `systemd/` | Scheduling units for Raspberry Pi (systemd) |
| `requirements.txt` | Python dependencies |

---

## Scheduling on Raspberry Pi

Copy the systemd units and enable them:

```bash
sudo cp systemd/stockml-collect.{service,timer} /etc/systemd/system/
sudo cp systemd/stockml-label.{service,timer}   /etc/systemd/system/

# Edit paths in the .service files to match your Pi setup, then:
sudo systemctl daemon-reload
sudo systemctl enable --now stockml-collect.timer
sudo systemctl enable --now stockml-label.timer

# Check status
systemctl list-timers --all | grep stockml
journalctl -u stockml-collect -f
```

`train.py` is run manually (or via a weekly timer you add) once enough data accrues.

---

## Model Validation

- **Walk-forward only** — never random train/test splits on time series data.
  Random splits leak future prices into training, producing falsely optimistic metrics.
- **Class imbalance handled** via `scale_pos_weight` (most weeks, most stocks don't pop 5%).
- **Metrics reported:** precision, recall, AUC on the *buy class* — never raw accuracy
  (accuracy is misleading when positives are rare).
- **Baseline printed:** "always predict buy" precision = base rate. The model
  must beat this to add any value. A warning is printed if it doesn't.

---

## Data Sources

- **yfinance** (primary) — free, no API key, ARM-friendly.
- **Finnhub** (fallback) — free key at finnhub.io; also used for optional news counts.
  Set `FINNHUB_API_KEY` in `.env`.

All external calls retry with exponential backoff (tenacity). One ticker failing
does not abort the run for remaining tickers.
