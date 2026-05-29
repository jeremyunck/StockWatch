"""Discord webhook embed builder and poster."""

import logging
import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

COLORS = {
    "LEAN_BUY":  3066993,   # green
    "HOLD":      9807270,   # grey
    "LEAN_SELL": 15158332,  # red
}

SIGNAL_LABELS = {
    "LEAN_BUY":  "LEAN BUY",
    "HOLD":      "HOLD",
    "LEAN_SELL": "LEAN SELL",
}

MAX_FIELD_VALUE = 1024
MAX_EMBED_CHARS = 6000
MAX_EMBEDS_PER_POST = 10


def _truncate(text: str, limit: int = MAX_FIELD_VALUE) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _fmt_price(price: float, change: float, change_pct: float) -> str:
    arrow = "▲" if change >= 0 else "▼"
    sign = "+" if change >= 0 else ""
    return f"${price:,.2f}  {arrow} {sign}{change_pct:.2f}%"


def _fmt_indicator_field(ind: dict, price: float) -> str:
    lines = []
    sma50 = ind.get("sma50")
    sma200 = ind.get("sma200")
    ema20 = ind.get("ema20")
    above200 = ind.get("price_above_sma200")

    if sma200 is not None:
        marker = "↑" if above200 else "↓"
        lines.append(f"SMA50: {sma50:.2f}  SMA200: {sma200:.2f} {marker}")
    if ema20 is not None:
        rising = ind.get("ema20_rising")
        trend_str = "rising" if rising else "falling" if rising is False else "—"
        lines.append(f"EMA20: {ema20:.2f} ({trend_str})")
    return "\n".join(lines) or "—"


def _fmt_momentum_field(ind: dict) -> str:
    lines = []
    rsi = ind.get("rsi14")
    if rsi is not None:
        zone = ind.get("rsi_zone", "")
        lines.append(f"RSI14: {rsi:.1f} ({zone})")
    macd = ind.get("macd")
    macd_sig = ind.get("macd_signal")
    cross = ind.get("macd_cross")
    if macd is not None:
        cross_str = f" [{cross}]" if cross and cross not in ("none",) else ""
        lines.append(f"MACD: {macd:.3f}  Signal: {macd_sig:.3f}{cross_str}" if macd_sig else f"MACD: {macd:.3f}")
    bb_pos = ind.get("bb_position")
    bb_sq = ind.get("bb_squeeze")
    if bb_pos:
        sq_str = " (squeeze)" if bb_sq else ""
        lines.append(f"BBands: {bb_pos}{sq_str}")
    return "\n".join(lines) or "—"


def _fmt_volatility_field(ind: dict) -> str:
    lines = []
    atr = ind.get("atr14")
    if atr is not None:
        lines.append(f"ATR14: {atr:.2f}")
    vol_ratio = ind.get("volume_ratio")
    obv_trend = ind.get("obv_trend")
    if vol_ratio is not None:
        lines.append(f"Vol ratio: {vol_ratio:.2f}x avg")
    if obv_trend:
        lines.append(f"OBV: {obv_trend}")
    return "\n".join(lines) or "—"


def _fmt_news_field(news: list[dict], max_items: int = 3) -> str:
    if not news:
        return "No recent headlines"
    lines = []
    for item in news[:max_items]:
        headline = item.get("headline", "").strip()
        url = item.get("url", "").strip()
        source = item.get("source", "")
        if headline and url:
            src_str = f" ({source})" if source else ""
            lines.append(f"[{headline}]({url}){src_str}")
        elif headline:
            lines.append(headline)
    return "\n".join(lines) or "No recent headlines"


def build_embed(
    ticker: str,
    name: str,
    quote: dict,
    indicators: dict,
    label: str,
    news: list[dict],
    llm_read: Optional[str],
    llm_label: Optional[str] = None,
) -> dict:
    """Build a single Discord embed for one ticker."""
    price = quote["price"]
    change = quote["change"]
    change_pct = quote["change_pct"]

    color = COLORS.get(label, 9807270)
    signal_display = SIGNAL_LABELS.get(label, label)

    # Flag LLM/indicator disagreement
    disagree_note = ""
    if llm_label and llm_label != label:
        disagree_note = f"  ⚠️ LLM: {SIGNAL_LABELS.get(llm_label, llm_label)}"

    now_et = datetime.now(tz=ET).strftime("%I:%M %p ET")

    embed = {
        "title": f"{ticker} — {_fmt_price(price, change, change_pct)}",
        "color": color,
        "fields": [
            {
                "name": f"Signal: {signal_display}{disagree_note}",
                "value": _truncate(f"**{name}**"),
                "inline": False,
            },
            {
                "name": "Trend",
                "value": _truncate(_fmt_indicator_field(indicators, price)),
                "inline": True,
            },
            {
                "name": "Momentum",
                "value": _truncate(_fmt_momentum_field(indicators)),
                "inline": True,
            },
            {
                "name": "Volatility / Volume",
                "value": _truncate(_fmt_volatility_field(indicators)),
                "inline": True,
            },
            {
                "name": "Recent News",
                "value": _truncate(_fmt_news_field(news)),
                "inline": False,
            },
        ],
        "footer": {"text": f"Educational only · not investment advice · {now_et}"},
    }

    if llm_read:
        embed["fields"].append({
            "name": "AI Read (not advice)",
            "value": _truncate(llm_read),
            "inline": False,
        })

    # Ensure no empty field name or value (causes Discord 400)
    for field in embed["fields"]:
        if not field.get("name"):
            field["name"] = "​"
        if not field.get("value"):
            field["value"] = "—"

    return embed


def post_embeds(webhook_url: str, embeds: list[dict], max_per_post: int = MAX_EMBEDS_PER_POST) -> None:
    """Post embeds to Discord webhook in batches, respecting rate limits."""
    if not embeds:
        return

    batches = _batch_embeds(embeds, max_per_post)
    for i, batch in enumerate(batches):
        if i > 0:
            time.sleep(1.0)
        _post_batch(webhook_url, batch)


def _batch_embeds(embeds: list[dict], max_per_post: int) -> list[list[dict]]:
    """Split embeds into batches respecting Discord's per-message limits."""
    batches = []
    current: list[dict] = []
    current_chars = 0

    for embed in embeds:
        embed_chars = _estimate_embed_chars(embed)
        if current and (len(current) >= max_per_post or current_chars + embed_chars > MAX_EMBED_CHARS):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(embed)
        current_chars += embed_chars

    if current:
        batches.append(current)
    return batches


def _estimate_embed_chars(embed: dict) -> int:
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    for field in embed.get("fields", []):
        total += len(field.get("name", "")) + len(field.get("value", ""))
    total += len((embed.get("footer") or {}).get("text", ""))
    return total


def _post_batch(webhook_url: str, embeds: list[dict], retries: int = 3) -> None:
    payload = {"embeds": embeds}
    for attempt in range(retries):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=15)
            if resp.status_code == 429:
                retry_after = resp.json().get("retry_after", 2.0)
                logger.warning("Discord rate-limited; sleeping %.1fs", retry_after)
                time.sleep(float(retry_after))
                continue
            resp.raise_for_status()
            logger.info("Posted %d embed(s) to Discord", len(embeds))
            return
        except requests.RequestException as exc:
            wait = 2 ** attempt
            logger.warning("Discord post failed (attempt %d): %s; retrying in %ds", attempt + 1, exc, wait)
            time.sleep(wait)

    logger.error("Failed to post embeds to Discord after %d attempts", retries)
