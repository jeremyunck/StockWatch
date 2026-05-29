"""Technical indicator computation and signal derivation using pandas-ta."""

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

try:
    import pandas_ta as ta
    _HAS_PANDAS_TA = True
except ImportError:
    _HAS_PANDAS_TA = False
    logger.warning("pandas-ta not available; indicators will be limited")


def compute_indicators(df: pd.DataFrame, price: float) -> dict:
    """Compute all configured indicators from a daily OHLC DataFrame.

    Returns a flat dict of indicator values and derived booleans.
    """
    ind: dict = {}

    if not _HAS_PANDAS_TA:
        return _fallback_indicators(df, price)

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # ----- SMAs -----
    sma50_s = ta.sma(close, length=50)
    sma200_s = ta.sma(close, length=200)
    ind["sma50"] = _last(sma50_s)
    ind["sma200"] = _last(sma200_s)
    ind["price_above_sma200"] = (price > ind["sma200"]) if ind["sma200"] else None
    ind["price_above_sma50"] = (price > ind["sma50"]) if ind["sma50"] else None

    # ----- EMA20 -----
    ema20_s = ta.ema(close, length=20)
    ind["ema20"] = _last(ema20_s)
    if ema20_s is not None and len(ema20_s.dropna()) >= 3:
        recent = ema20_s.dropna().iloc[-3:]
        ind["ema20_rising"] = bool(recent.iloc[-1] > recent.iloc[0])
    else:
        ind["ema20_rising"] = None

    # ----- RSI14 -----
    rsi_s = ta.rsi(close, length=14)
    rsi_val = _last(rsi_s)
    ind["rsi14"] = rsi_val
    if rsi_val is not None:
        if rsi_val < 30:
            ind["rsi_zone"] = "oversold"
        elif rsi_val > 70:
            ind["rsi_zone"] = "overbought"
        else:
            ind["rsi_zone"] = "neutral"
    else:
        ind["rsi_zone"] = None

    # RSI trend (rising over last 5 bars)
    if rsi_s is not None and len(rsi_s.dropna()) >= 5:
        r = rsi_s.dropna().iloc[-5:]
        ind["rsi_rising"] = bool(r.iloc[-1] > r.iloc[0])
    else:
        ind["rsi_rising"] = None

    # ----- MACD -----
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        cols = macd_df.columns.tolist()
        macd_col = next((c for c in cols if c.startswith("MACD_") and "s" not in c.lower()[5:] and "h" not in c.lower()[5:]), cols[0])
        sig_col = next((c for c in cols if "MACDs" in c or c.startswith("MACDs")), None)
        hist_col = next((c for c in cols if "MACDh" in c or c.startswith("MACDh")), None)
        ind["macd"] = _last(macd_df[macd_col]) if macd_col else None
        ind["macd_signal"] = _last(macd_df[sig_col]) if sig_col else None
        ind["macd_hist"] = _last(macd_df[hist_col]) if hist_col else None
        ind["macd_cross"] = _macd_cross(macd_df, macd_col, sig_col)
    else:
        ind["macd"] = ind["macd_signal"] = ind["macd_hist"] = ind["macd_cross"] = None

    # ----- Bollinger Bands -----
    bb_df = ta.bbands(close, length=20, std=2)
    if bb_df is not None and not bb_df.empty:
        bb_cols = bb_df.columns.tolist()
        lower_col = next((c for c in bb_cols if "BBL" in c), None)
        mid_col = next((c for c in bb_cols if "BBM" in c), None)
        upper_col = next((c for c in bb_cols if "BBU" in c), None)
        bw_col = next((c for c in bb_cols if "BBB" in c), None)
        bb_lower = _last(bb_df[lower_col]) if lower_col else None
        bb_mid = _last(bb_df[mid_col]) if mid_col else None
        bb_upper = _last(bb_df[upper_col]) if upper_col else None
        bb_bw = _last(bb_df[bw_col]) if bw_col else None
        ind["bb_lower"] = bb_lower
        ind["bb_mid"] = bb_mid
        ind["bb_upper"] = bb_upper
        ind["bb_bw"] = bb_bw
        if all(v is not None for v in [bb_lower, bb_upper]):
            if price <= bb_lower:
                ind["bb_position"] = "lower"
            elif price >= bb_upper:
                ind["bb_position"] = "upper"
            else:
                ind["bb_position"] = "mid"
        else:
            ind["bb_position"] = None
        # squeeze: bandwidth in lowest 20% of its recent range
        if bw_col and len(bb_df[bw_col].dropna()) >= 20:
            bw_series = bb_df[bw_col].dropna().iloc[-20:]
            bw_now = bw_series.iloc[-1]
            ind["bb_squeeze"] = bool(bw_now <= bw_series.quantile(0.2))
        else:
            ind["bb_squeeze"] = None
    else:
        ind["bb_lower"] = ind["bb_mid"] = ind["bb_upper"] = None
        ind["bb_position"] = ind["bb_squeeze"] = None

    # ----- ATR14 -----
    atr_s = ta.atr(high, low, close, length=14)
    ind["atr14"] = _last(atr_s)

    # ----- OBV -----
    obv_s = ta.obv(close, volume)
    ind["obv"] = _last(obv_s)
    if obv_s is not None and len(obv_s.dropna()) >= 20:
        recent_obv = obv_s.dropna().iloc[-20:]
        ind["obv_trend"] = "rising" if recent_obv.iloc[-1] > recent_obv.iloc[0] else "falling"
    else:
        ind["obv_trend"] = None

    # Volume vs 20-day average
    if len(volume) >= 20:
        vol_avg = float(volume.iloc[-20:].mean())
        vol_now = float(volume.iloc[-1])
        ind["volume_ratio"] = vol_now / vol_avg if vol_avg else None
    else:
        ind["volume_ratio"] = None

    return ind


def _last(series) -> Optional[float]:
    if series is None:
        return None
    clean = series.dropna()
    if clean.empty:
        return None
    return float(clean.iloc[-1])


def _macd_cross(macd_df: pd.DataFrame, macd_col: str, sig_col: Optional[str]) -> Optional[str]:
    if sig_col is None:
        return None
    sub = macd_df[[macd_col, sig_col]].dropna()
    if len(sub) < 2:
        return None
    prev_diff = sub[macd_col].iloc[-2] - sub[sig_col].iloc[-2]
    curr_diff = sub[macd_col].iloc[-1] - sub[sig_col].iloc[-1]
    if prev_diff < 0 and curr_diff >= 0:
        return "bullish"
    if prev_diff > 0 and curr_diff <= 0:
        return "bearish"
    return "none" if curr_diff >= 0 else "below"


def _fallback_indicators(df: pd.DataFrame, price: float) -> dict:
    """Minimal indicator set using only pandas when pandas-ta is unavailable."""
    close = df["Close"]
    ind: dict = {}
    ind["sma50"] = float(close.iloc[-50:].mean()) if len(close) >= 50 else None
    ind["sma200"] = float(close.iloc[-200:].mean()) if len(close) >= 200 else None
    ind["ema20"] = None
    ind["ema20_rising"] = None
    ind["rsi14"] = _manual_rsi(close, 14)
    ind["rsi_zone"] = None
    ind["rsi_rising"] = None
    ind["macd"] = ind["macd_signal"] = ind["macd_hist"] = ind["macd_cross"] = None
    ind["bb_lower"] = ind["bb_mid"] = ind["bb_upper"] = ind["bb_position"] = None
    ind["bb_squeeze"] = ind["bb_bw"] = None
    ind["atr14"] = None
    ind["obv"] = ind["obv_trend"] = None
    ind["volume_ratio"] = None
    ind["price_above_sma200"] = (price > ind["sma200"]) if ind["sma200"] else None
    ind["price_above_sma50"] = (price > ind["sma50"]) if ind["sma50"] else None
    return ind


def _manual_rsi(close: pd.Series, period: int = 14) -> Optional[float]:
    if len(close) < period + 1:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def derive_signal(ind: dict, price: float) -> str:
    """Deterministic confluence signal: LEAN_BUY / HOLD / LEAN_SELL.

    Rules require multiple confirming indicators to avoid noise.
    """
    buy_score = 0
    sell_score = 0

    # Trend
    if ind.get("price_above_sma200") is True:
        buy_score += 1
    elif ind.get("price_above_sma200") is False:
        sell_score += 1

    if ind.get("ema20_rising") is True:
        buy_score += 1
    elif ind.get("ema20_rising") is False:
        sell_score += 1

    # Momentum: RSI
    rsi = ind.get("rsi14")
    rsi_rising = ind.get("rsi_rising")
    if rsi is not None:
        if 30 <= rsi <= 60 and rsi_rising:
            buy_score += 1
        elif rsi > 70:
            sell_score += 1
        elif rsi < 30:
            # oversold bounce potential — slight buy lean
            buy_score += 0  # neutral: don't blindly buy

    # MACD cross
    macd_cross = ind.get("macd_cross")
    if macd_cross == "bullish":
        buy_score += 2
    elif macd_cross == "bearish":
        sell_score += 2

    # Volume expansion supports direction
    vol_ratio = ind.get("volume_ratio")
    if vol_ratio and vol_ratio > 1.3:
        # volume confirming whichever side is winning
        if buy_score > sell_score:
            buy_score += 1
        elif sell_score > buy_score:
            sell_score += 1

    # OBV trend
    if ind.get("obv_trend") == "rising":
        buy_score += 1
    elif ind.get("obv_trend") == "falling":
        sell_score += 1

    # Bollinger band position
    bb_pos = ind.get("bb_position")
    if bb_pos == "lower":
        buy_score += 1
    elif bb_pos == "upper":
        sell_score += 1

    # ----- Decision -----
    margin = 3
    if buy_score >= margin and buy_score > sell_score + 1:
        return "LEAN_BUY"
    if sell_score >= margin and sell_score > buy_score + 1:
        return "LEAN_SELL"
    return "HOLD"
