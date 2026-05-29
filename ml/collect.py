"""
collect.py — Populate price_history and snapshots for the buy-signal pipeline.

Two modes:
  --init   Backfill ~1 year of daily OHLC into price_history for every watchlist
           ticker (≥252 trading days to warm up a 200-day SMA). Run once before
           starting the 30-minute polling schedule.

  (normal) Append today's completed bar to price_history, then write one
           snapshots row per ticker with raw indicator values computed via
           pandas-ta. Intended to run every 30 min during market hours.

Data sources (in priority order):
  1. yfinance  — free, no key, ARM-friendly. Primary for daily history.
  2. Finnhub   — free key (FINNHUB_API_KEY in .env), 60 req/min candle endpoint.
                 Used as fallback when yfinance fails.

Indicator library: pandas-ta (pure Python, no TA-Lib C dependency — installs
cleanly on ARM/aarch64).

Usage:
    python collect.py --db state.db --config config.yaml --init
    python collect.py --db state.db --config config.yaml
    python collect.py --db state.db --config config.yaml --dry-run
"""

import argparse
import logging
import sqlite3
import time
from datetime import datetime, timezone

import pandas as pd
import pandas_ta as ta
import yaml
from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HISTORY_YEARS = 1        # backfill period for --init
INTER_TICKER_SLEEP = 2   # seconds between tickers (avoid throttling)
NY_TZ = "America/New_York"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def init_db(conn):
    """Apply schema.sql if tables don't exist yet."""
    import os
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as fh:
        conn.executescript(fh.read())
    conn.commit()


def upsert_bars(conn, ticker: str, df: pd.DataFrame):
    """
    Insert or replace daily OHLC rows.  df must have columns:
    Date (index or column), Open, High, Low, Close, Volume.
    """
    rows = []
    for idx, row in df.iterrows():
        bar_date = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
        rows.append((
            ticker,
            bar_date,
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
            int(row["Volume"]),
        ))
    conn.executemany(
        """INSERT OR REPLACE INTO price_history
           (ticker, bar_date, open, high, low, close, volume)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def latest_bar_date(conn, ticker: str):
    """Return the most recent bar_date stored, or None."""
    row = conn.execute(
        "SELECT MAX(bar_date) FROM price_history WHERE ticker = ?", (ticker,)
    ).fetchone()
    return row[0] if row else None


def insert_snapshot(conn, snap: dict, dry_run: bool = False):
    if dry_run:
        log.info("[dry-run] snapshot: %s", snap)
        return
    conn.execute(
        """INSERT OR IGNORE INTO snapshots
           (ticker, captured_at, bar_date, entry_price,
            rsi14, macd_line, macd_signal, macd_hist,
            sma50, sma200, ema20,
            bb_upper, bb_lower, bb_mid,
            atr14, vol_avg20, obv,
            ret_5d, ret_20d, hi_52w, lo_52w, news_count_24h)
           VALUES
           (:ticker, :captured_at, :bar_date, :entry_price,
            :rsi14, :macd_line, :macd_signal, :macd_hist,
            :sma50, :sma200, :ema20,
            :bb_upper, :bb_lower, :bb_mid,
            :atr14, :vol_avg20, :obv,
            :ret_5d, :ret_20d, :hi_52w, :lo_52w, :news_count_24h)""",
        snap,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Data fetchers (with retry)
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _yf_download(ticker: str, period: str, interval: str = "1d") -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(ticker, period=period, interval=interval,
                     auto_adjust=True, progress=False)
    if df is None or df.empty:
        raise ValueError(f"yfinance returned empty data for {ticker}")
    # yfinance sometimes returns MultiIndex columns; flatten
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _finnhub_candles(ticker: str, from_ts: int, to_ts: int) -> pd.DataFrame:
    import os
    import finnhub
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key:
        raise EnvironmentError("FINNHUB_API_KEY not set")
    client = finnhub.Client(api_key=key)
    res = client.stock_candles(ticker, "D", from_ts, to_ts)
    if res.get("s") != "ok" or not res.get("t"):
        raise ValueError(f"Finnhub returned no data for {ticker}: {res.get('s')}")
    df = pd.DataFrame({
        "Open":   res["o"],
        "High":   res["h"],
        "Low":    res["l"],
        "Close":  res["c"],
        "Volume": res["v"],
    }, index=pd.to_datetime(res["t"], unit="s", utc=True).tz_convert(NY_TZ).normalize())
    df.index = df.index.tz_localize(None)
    return df


def fetch_history(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Fetch daily OHLCV; yfinance first, Finnhub as fallback."""
    try:
        df = _yf_download(ticker, period=period)
        log.info("%s: yfinance returned %d bars", ticker, len(df))
        return df
    except Exception as yf_err:
        log.warning("%s: yfinance failed (%s); trying Finnhub", ticker, yf_err)

    import calendar
    now = int(time.time())
    days = 365 if period == "1y" else 30
    from_ts = now - days * 86400
    try:
        df = _finnhub_candles(ticker, from_ts, now)
        log.info("%s: Finnhub returned %d bars", ticker, len(df))
        return df
    except Exception as fh_err:
        raise RuntimeError(
            f"{ticker}: both yfinance and Finnhub failed. "
            f"yfinance: {yf_err}; Finnhub: {fh_err}"
        ) from fh_err


# ---------------------------------------------------------------------------
# Indicator computation
# ---------------------------------------------------------------------------

def compute_indicators(df: pd.DataFrame) -> dict:
    """
    Compute all raw indicator values from a full price DataFrame.
    Returns a dict of scalar values for the *most recent* bar.
    Raises if the DataFrame is too short to warm up the slowest indicator (SMA-200).
    """
    if len(df) < 200:
        raise ValueError(
            f"Need ≥200 bars to compute SMA-200; got {len(df)}. "
            "Run --init to backfill history first."
        )

    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    vol   = df["Volume"]

    # pandas-ta appends columns to a copy; work on a local DataFrame
    work = df[["Open", "High", "Low", "Close", "Volume"]].copy()

    rsi   = ta.rsi(close, length=14)
    macd  = ta.macd(close, fast=12, slow=26, signal=9)
    sma50 = ta.sma(close, length=50)
    sma200 = ta.sma(close, length=200)
    ema20 = ta.ema(close, length=20)
    bb    = ta.bbands(close, length=20, std=2)
    atr   = ta.atr(high, low, close, length=14)
    obv_s = ta.obv(close, vol)

    vol_avg20 = vol.rolling(20).mean()
    ret_5d  = close.pct_change(5)
    ret_20d = close.pct_change(20)
    hi_52w  = close.rolling(252).max()
    lo_52w  = close.rolling(252).min()

    def last(series):
        if series is None or series.empty:
            return None
        v = series.iloc[-1]
        return None if pd.isna(v) else float(v)

    # MACD column names from pandas-ta: MACD_12_26_9, MACDs_12_26_9, MACDh_12_26_9
    macd_line   = last(macd.get("MACD_12_26_9"))   if macd is not None else None
    macd_signal = last(macd.get("MACDs_12_26_9"))  if macd is not None else None
    macd_hist   = last(macd.get("MACDh_12_26_9"))  if macd is not None else None

    # BB column names: BBL_20_2.0, BBM_20_2.0, BBU_20_2.0
    bb_lower = last(bb.get("BBL_20_2.0")) if bb is not None else None
    bb_mid   = last(bb.get("BBM_20_2.0")) if bb is not None else None
    bb_upper = last(bb.get("BBU_20_2.0")) if bb is not None else None

    return {
        "entry_price": last(close),
        "rsi14":       last(rsi),
        "macd_line":   macd_line,
        "macd_signal": macd_signal,
        "macd_hist":   macd_hist,
        "sma50":       last(sma50),
        "sma200":      last(sma200),
        "ema20":       last(ema20),
        "bb_upper":    bb_upper,
        "bb_lower":    bb_lower,
        "bb_mid":      bb_mid,
        "atr14":       last(atr),
        "vol_avg20":   last(vol_avg20),
        "obv":         last(obv_s),
        "ret_5d":      last(ret_5d),
        "ret_20d":     last(ret_20d),
        "hi_52w":      last(hi_52w),
        "lo_52w":      last(lo_52w),
    }


# ---------------------------------------------------------------------------
# News count (optional, defaults to 0 on any failure)
# ---------------------------------------------------------------------------

def news_count_24h(ticker: str) -> int:
    """Count distinct headlines for ticker in the last 24 hours via Finnhub."""
    import os
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key:
        return 0
    try:
        import finnhub
        from datetime import timedelta
        client = finnhub.Client(api_key=key)
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(hours=24)
        news = client.company_news(
            ticker,
            _from=yesterday.strftime("%Y-%m-%d"),
            to=now.strftime("%Y-%m-%d"),
        )
        return len({n["headline"] for n in news}) if news else 0
    except Exception as exc:
        log.debug("%s: news_count_24h failed: %s", ticker, exc)
        return 0


# ---------------------------------------------------------------------------
# Init mode: backfill ~1y of price_history
# ---------------------------------------------------------------------------

def run_init(conn, tickers: list, dry_run: bool):
    log.info("=== INIT MODE: backfilling ~1y of price history ===")
    for ticker in tickers:
        try:
            df = fetch_history(ticker, period=f"{HISTORY_YEARS}y")
            if not dry_run:
                n = upsert_bars(conn, ticker, df)
                log.info("%s: stored %d bars", ticker, n)
            else:
                log.info("[dry-run] %s: would store %d bars", ticker, len(df))
        except Exception as exc:
            log.error("%s: init failed — %s", ticker, exc)
        time.sleep(INTER_TICKER_SLEEP)
    log.info("=== INIT COMPLETE ===")


# ---------------------------------------------------------------------------
# Normal mode: append today's bar + write snapshot
# ---------------------------------------------------------------------------

def run_collect(conn, tickers: list, dry_run: bool):
    """
    For each ticker:
    1. Refresh price_history with the latest completed trading day.
    2. Load the full stored history, compute indicators, write a snapshot.

    One ticker failing must not abort the run for the rest.
    """
    now_utc = datetime.now(timezone.utc)
    bar_date = _market_bar_date()
    captured_at = now_utc.isoformat()

    for ticker in tickers:
        try:
            _collect_one(conn, ticker, bar_date, captured_at, dry_run)
        except Exception as exc:
            log.error("%s: collect failed — %s", ticker, exc)
        time.sleep(INTER_TICKER_SLEEP)


def _market_bar_date() -> str:
    """Return today's date in US/Eastern as 'YYYY-MM-DD'."""
    import zoneinfo
    ny = zoneinfo.ZoneInfo(NY_TZ)
    return datetime.now(ny).strftime("%Y-%m-%d")


def _collect_one(conn, ticker: str, bar_date: str, captured_at: str, dry_run: bool):
    # --- 1. Refresh price_history with latest bar ---------------------------
    existing = latest_bar_date(conn, ticker)
    if existing == bar_date:
        log.debug("%s: today's bar already in price_history", ticker)
    else:
        df_new = fetch_history(ticker, period="5d")  # small fetch for latest bars
        if not dry_run:
            n = upsert_bars(conn, ticker, df_new)
            log.info("%s: refreshed price_history (+%d bars)", ticker, n)

    # --- 2. Load full history from DB for indicator computation -------------
    df_hist = pd.read_sql_query(
        "SELECT bar_date, open, high, low, close, volume "
        "FROM price_history WHERE ticker = ? ORDER BY bar_date ASC",
        conn,
        params=(ticker,),
    )
    if df_hist.empty:
        raise RuntimeError(f"{ticker}: no price_history — run --init first")

    df_hist.index = pd.to_datetime(df_hist["bar_date"])
    df_hist = df_hist.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    })

    # --- 3. Compute indicators ----------------------------------------------
    indic = compute_indicators(df_hist)

    # --- 4. Fetch news count (optional; 0 on failure) -----------------------
    count = news_count_24h(ticker)

    snap = {
        "ticker":         ticker,
        "captured_at":    captured_at,
        "bar_date":       bar_date,
        "news_count_24h": count,
        **indic,
    }

    insert_snapshot(conn, snap, dry_run=dry_run)
    log.info(
        "%s: snapshot written — price=%.2f rsi=%.1f",
        ticker,
        indic["entry_price"] or 0,
        indic["rsi14"] or 0,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_tickers(config_path: str) -> list:
    with open(config_path) as fh:
        cfg = yaml.safe_load(fh)
    items = cfg.get("watchlist", [])
    tickers = []
    for item in items:
        if isinstance(item, dict):
            tickers.append(item["ticker"])
        else:
            tickers.append(str(item))
    return tickers


def main():
    ap = argparse.ArgumentParser(
        description="Collect price_history + snapshots for the buy-signal pipeline."
    )
    ap.add_argument("--db", required=True, help="Path to SQLite database")
    ap.add_argument("--config", default="config.yaml",
                    help="Path to config.yaml with watchlist")
    ap.add_argument("--init", action="store_true",
                    help="Backfill ~1y of price_history (run once before scheduling)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch and compute but do not write to the database")
    args = ap.parse_args()

    tickers = load_tickers(args.config)
    log.info("Watchlist: %s", tickers)

    conn = sqlite3.connect(args.db)
    init_db(conn)

    try:
        if args.init:
            run_init(conn, tickers, dry_run=args.dry_run)
        else:
            run_collect(conn, tickers, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
