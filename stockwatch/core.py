"""StockWatch core module - fetch stock prices and technical indicators via yfinance."""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
import json

import yfinance as yf

# Default tickers to watch
DEFAULT_TICKERS = ["NVDA", "AMD", "MU"]

# Technical indicators to compute
TECHNICAL_INDICATORS = [
    "SMA_20",      # Simple Moving Average (20-day)
    "SMA_50",      # Simple Moving Average (50-day)
    "RSI_14",      # Relative Strength Index (14-day)
    "MACD",        # Moving Average Convergence Divergence
    "MACD_signal", # MACD signal line
    "BB_upper",    # Bollinger Band (upper)
    "BB_lower",    # Bollinger Band (lower)
    "BB_mid",      # Bollinger Band (mid / SMA 20)
]


@dataclass
class TechnicalIndicators:
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    rsi_14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_mid: Optional[float] = None
    volume_avg_20: Optional[float] = None


@dataclass
class StockQuote:
    ticker: str
    price: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    previous_close: Optional[float] = None
    open: Optional[float] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    volume: Optional[int] = None
    market_cap: Optional[float] = None
    pe_ratio: Optional[float] = None
    dividend_yield: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    trend_1d_change_pct: Optional[float] = None
    trend_1w_change_pct: Optional[float] = None
    trend_1m_change_pct: Optional[float] = None
    technical: TechnicalIndicators = field(default_factory=TechnicalIndicators)
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat())
    error: Optional[str] = None


def compute_indicators(ticker_obj) -> TechnicalIndicators:
    """Compute technical indicators from historical price data."""
    indicators = TechnicalIndicators()

    try:
        hist = ticker_obj.history(period="3mo")
        if hist.empty or len(hist) < 15:
            return indicators

        close = hist["Close"]
        volume = hist["Volume"]

        # SMA 20
        if len(close) >= 20:
            indicators.sma_20 = round(close.rolling(20).mean().iloc[-1], 2)

        # SMA 50
        if len(close) >= 50:
            indicators.sma_50 = round(close.rolling(50).mean().iloc[-1], 2)

        # RSI 14
        if len(close) >= 15:
            delta = close.diff()
            gain = delta.where(delta > 0, 0.0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
            rs = gain / loss
            indicators.rsi_14 = round(100 - (100 / (1 + rs.iloc[-1])), 2)

        # MACD
        if len(close) >= 26:
            ema_12 = close.ewm(span=12, adjust=False).mean()
            ema_26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema_12 - ema_26
            signal = macd_line.ewm(span=9, adjust=False).mean()
            indicators.macd = round(macd_line.iloc[-1], 4)
            indicators.macd_signal = round(signal.iloc[-1], 4)

        # Bollinger Bands (20-day SMA ± 2σ)
        if len(close) >= 20:
            sma_20 = close.rolling(20).mean()
            std_20 = close.rolling(20).std()
            indicators.bb_mid = round(sma_20.iloc[-1], 2)
            indicators.bb_upper = round(sma_20.iloc[-1] + 2 * std_20.iloc[-1], 2)
            indicators.bb_lower = round(sma_20.iloc[-1] - 2 * std_20.iloc[-1], 2)

        # 20-day avg volume
        if len(volume) >= 20:
            indicators.volume_avg_20 = round(volume.rolling(20).mean().iloc[-1], 0)

        # Trend: 1d, 1w, 1m change %
        if len(close) >= 2:
            quote.trend_1d_change_pct = round(((close.iloc[-1] / close.iloc[-2]) - 1) * 100, 2)
        if len(close) >= 5:
            quote.trend_1w_change_pct = round(((close.iloc[-1] / close.iloc[-5]) - 1) * 100, 2)
        if len(close) >= 20:
            quote.trend_1m_change_pct = round(((close.iloc[-1] / close.iloc[-20]) - 1) * 100, 2)

    except Exception as e:
        # Log the error for debugging, but still return partial indicators
        import logging
        logging.warning(f"Failed to compute indicators: {e}")

    return indicators


def get_quote(ticker: str) -> StockQuote:
    """Fetch a single stock quote with technical indicators."""
    quote = StockQuote(ticker=ticker)

    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}

        # Current price — try multiple sources
        price = (
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("previousClose")
        )

        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        change_val = info.get("regularMarketChange")
        change_pct_val = info.get("regularMarketChangePercent")

        # If yfinance info is sparse, try fast_info
        if not price:
            try:
                fast = stock.fast_info
                price = getattr(fast, "last_price", None) or getattr(fast, "regular_market_previous_close", None)
            except Exception:
                pass

        quote.price = round(price, 2) if price else None
        quote.previous_close = round(prev_close, 2) if prev_close else None
        quote.change = round(change_val, 2) if change_val else None
        quote.change_pct = round(change_pct_val, 4) if change_pct_val else None
        quote.open = round(info.get("regularMarketOpen"), 2) if info.get("regularMarketOpen") else None
        quote.day_high = round(info.get("regularMarketDayHigh"), 2) if info.get("regularMarketDayHigh") else None
        quote.day_low = round(info.get("regularMarketDayLow"), 2) if info.get("regularMarketDayLow") else None
        quote.volume = info.get("regularMarketVolume") or info.get("volume")
        quote.market_cap = info.get("marketCap")
        quote.pe_ratio = round(info.get("trailingPE"), 2) if info.get("trailingPE") else None
        quote.dividend_yield = round(info.get("dividendYield") * 100, 2) if info.get("dividendYield") else None
        quote.fifty_two_week_high = round(info.get("fiftyTwoWeekHigh"), 2) if info.get("fiftyTwoWeekHigh") else None
        quote.fifty_two_week_low = round(info.get("fiftyTwoWeekLow"), 2) if info.get("fiftyTwoWeekLow") else None

        # Technical indicators from historical data
        quote.technical = compute_indicators(stock)

        # Trend: 1d, 1w, 1m change % from historical close data
        try:
            hist = stock.history(period="1mo")
            if not hist.empty:
                close = hist["Close"]
                if len(close) >= 2:
                    quote.trend_1d_change_pct = round(((close.iloc[-1] / close.iloc[-2]) - 1) * 100, 2)
                if len(close) >= 5:
                    quote.trend_1w_change_pct = round(((close.iloc[-1] / close.iloc[-5]) - 1) * 100, 2)
                if len(close) >= 20:
                    quote.trend_1m_change_pct = round(((close.iloc[-1] / close.iloc[-20]) - 1) * 100, 2)
        except Exception as e:
            import logging
            logging.warning(f"Failed to compute trend: {e}")

    except Exception as e:
        quote.error = str(e)

    return quote


def get_quotes(tickers: list[str]) -> list[StockQuote]:
    """Fetch quotes for multiple tickers in parallel."""
    import concurrent.futures
    tickers = [t.strip().upper() for t in tickers if t.strip()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tickers), 5)) as executor:
        return list(executor.map(get_quote, tickers))


def format_table(quotes: list[StockQuote]) -> str:
    """Format quotes as a pretty terminal table."""
    lines = []
    lines.append("=" * 88)
    lines.append(f"{'TICKER':>6}  {'PRICE':>8}  {'CHANGE':>8}  {'%CHG':>7}  {'RSI(14)':>7}  {'SMA(20)':>8}  {'SMA(50)':>8}  {'MACD':>8}  {'VOL(20d)':>10}")
    lines.append("-" * 88)

    for q in quotes:
        if q.error:
            lines.append(f"{q.ticker:>6}  {'ERROR':>8}  {q.error}")
            continue

        price_s = f"${q.price:.2f}" if q.price is not None else "---"
        change_s = f"{q.change:+.2f}" if q.change is not None else "---"
        pct_s = f"{q.change_pct:+.2%}" if q.change_pct is not None else "---"
        rsi_s = f"{q.technical.rsi_14:.1f}" if q.technical.rsi_14 is not None else "---"
        sma20_s = f"${q.technical.sma_20:.2f}" if q.technical.sma_20 is not None else "---"
        sma50_s = f"${q.technical.sma_50:.2f}" if q.technical.sma_50 is not None else "---"
        macd_s = f"{q.technical.macd:.4f}" if q.technical.macd is not None else "---"
        vol_s = f"{q.technical.volume_avg_20:,.0f}" if q.technical.volume_avg_20 is not None else "---"

        lines.append(f"{q.ticker:>6}  {price_s:>8}  {change_s:>8}  {pct_s:>7}  {rsi_s:>7}  {sma20_s:>8}  {sma50_s:>8}  {macd_s:>8}  {vol_s:>10}")

    lines.append("=" * 88)
    lines.append(f"Fetched: {quotes[0].fetched_at if quotes else 'N/A'}")
    return "\n".join(lines)


def format_json(quotes: list[StockQuote]) -> str:
    """Format quotes as JSON."""
    return json.dumps([asdict(q) for q in quotes], indent=2, default=str)