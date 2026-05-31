"""OpenRouter LLM read and new-signal gate."""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import openai

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a markets analysis assistant providing educational summaries of technical and news data. You are NOT a financial adviser.

Rules:
- Use ONLY the data provided in the user message. Do not inject outside knowledge about the company.
- Output exactly three sections in this order:
  1. A 2-3 sentence summary of price action, indicator confluence, and notable news.
  2. One line: "Signal: HOLD | LEAN BUY | LEAN SELL — [one-sentence rationale tied to specific indicators]"
  3. One sentence risk caveat.
- End every response with: "Educational only — not investment advice."
- Never express certainty about future price direction. Use language like "suggests", "may", "historically associated with".
- Be concise. Total response should be under 200 words."""

_client: Optional[openai.OpenAI] = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPEN_ROUTER_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPEN_ROUTER_API_KEY not set")
        _client = openai.OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )
    return _client


def _build_user_message(ticker: str, name: str, quote: dict, indicators: dict, news: list[dict]) -> str:
    price = quote["price"]
    change_pct = quote["change_pct"]
    direction = "up" if change_pct >= 0 else "down"

    lines = [
        f"=== {ticker} ({name}) ===",
        f"Price: ${price:.2f}  ({direction} {abs(change_pct):.2f}% today)",
        "",
        "--- INDICATORS ---",
    ]

    def _fmt(key, val, precision=2):
        if val is None:
            return f"{key}: N/A"
        if isinstance(val, bool):
            return f"{key}: {'yes' if val else 'no'}"
        if isinstance(val, float):
            return f"{key}: {val:.{precision}f}"
        return f"{key}: {val}"

    lines += [
        _fmt("SMA50", indicators.get("sma50")),
        _fmt("SMA200", indicators.get("sma200")),
        _fmt("Price above SMA200", indicators.get("price_above_sma200")),
        _fmt("EMA20", indicators.get("ema20")),
        _fmt("EMA20 rising", indicators.get("ema20_rising")),
        _fmt("RSI14", indicators.get("rsi14"), precision=1),
        _fmt("RSI zone", indicators.get("rsi_zone")),
        _fmt("RSI rising", indicators.get("rsi_rising")),
        _fmt("MACD", indicators.get("macd"), precision=3),
        _fmt("MACD signal", indicators.get("macd_signal"), precision=3),
        _fmt("MACD hist", indicators.get("macd_hist"), precision=3),
        _fmt("MACD cross", indicators.get("macd_cross")),
        _fmt("BB position", indicators.get("bb_position")),
        _fmt("BB squeeze", indicators.get("bb_squeeze")),
        _fmt("ATR14", indicators.get("atr14")),
        _fmt("Volume ratio (vs 20d avg)", indicators.get("volume_ratio"), precision=2),
        _fmt("OBV trend", indicators.get("obv_trend")),
        "",
    ]

    lines.append("--- RECENT NEWS (last 24h) ---")
    if news:
        for item in news[:5]:
            ts = item.get("datetime")
            time_str = ""
            if ts:
                try:
                    time_str = " [" + datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d %H:%M UTC") + "]"
                except Exception:
                    pass
            src = item.get("source", "")
            src_str = f" ({src})" if src else ""
            lines.append(f"- {item['headline']}{src_str}{time_str}")
    else:
        lines.append("No recent news found.")

    return "\n".join(lines)


def get_llm_read(
    ticker: str,
    name: str,
    quote: dict,
    indicators: dict,
    news: list[dict],
    model: str = "anthropic/claude-haiku-4.5",
) -> tuple[Optional[str], Optional[str]]:
    """Call OpenRouter with the given model; return (full_text, parsed_signal_label).

    Returns (None, None) on failure — caller should degrade gracefully.
    Parsed signal is one of: LEAN_BUY, HOLD, LEAN_SELL, or None.
    """
    try:
        client = _get_client()
        user_msg = _build_user_message(ticker, name, quote, indicators, news)

        response = client.chat.completions.create(
            model=model,
            max_tokens=400,
            temperature=0.2,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )

        text = response.choices[0].message.content.strip()
        logger.debug("LLM response for %s: %s", ticker, text[:120])

        signal = _parse_signal(text)
        return text, signal

    except Exception as exc:
        logger.warning("LLM call failed for %s: %s", ticker, exc)
        return None, None


def _parse_signal(text: str) -> Optional[str]:
    """Extract LEAN_BUY / HOLD / LEAN_SELL from the LLM output."""
    upper = text.upper()
    if "LEAN BUY" in upper:
        return "LEAN_BUY"
    if "LEAN SELL" in upper:
        return "LEAN_SELL"
    if "HOLD" in upper:
        return "HOLD"
    return None


def should_call_llm(
    ticker: str,
    new_label: str,
    last_signal: Optional[dict],
    has_new_news: bool,
    only_on_new_signal: bool,
) -> bool:
    """Gate: decide whether to spend an LLM call this cycle."""
    if not only_on_new_signal:
        return True

    if has_new_news:
        return True

    if last_signal is None:
        return True

    if last_signal["label"] != new_label:
        return True

    return False
