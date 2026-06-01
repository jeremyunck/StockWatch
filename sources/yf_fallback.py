"""yfinance data source: OHLC history and real-time quote."""

import logging
import random
import time

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

_HEADERS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def get_ohlc(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """Download OHLC history via yfinance.

    Returns DataFrame with columns: Open, High, Low, Close, Volume.
    Raises if fewer than 50 rows returned (insufficient for indicators).
    """
    time.sleep(random.uniform(1.0, 3.0))
    t = yf.Ticker(ticker)
    df = t.history(period=period, interval=interval, auto_adjust=True)
    if df is None or len(df) < 50:
        raise ValueError(f"Insufficient OHLC data for {ticker}: got {len(df) if df is not None else 0} rows")
    # Flatten MultiIndex columns if present (yfinance sometimes returns tuples)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    # Ensure column names are strings, not tuples
    df.columns = [str(c) for c in df.columns]
    df.index = pd.to_datetime(df.index)
    return df[["Open", "High", "Low", "Close", "Volume"]].copy()


def get_quote(ticker: str) -> dict:
    """Pull a snapshot quote from yfinance."""
    try:
        time.sleep(random.uniform(1.0, 2.0))
        t = yf.Ticker(ticker)
        info = t.fast_info
        price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
        prev = getattr(info, "previous_close", None) or price
        if price is None:
            raise ValueError(f"No price in yfinance fast_info for {ticker}")
        change = price - prev
        change_pct = (change / prev * 100) if prev else 0.0
        return {
            "price": float(price),
            "prev_close": float(prev),
            "change": float(change),
            "change_pct": float(change_pct),
            "high": None,
            "low": None,
            "open": None,
            "timestamp": None,
        }
    except Exception as exc:
        logger.warning("yfinance fallback quote failed for %s: %s", ticker, exc)
        raise
