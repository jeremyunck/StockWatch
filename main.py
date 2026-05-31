"""StockWatch orchestrator — one execution = one full monitoring cycle."""

import hashlib
import logging
import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

import store
from discord_out import build_embed, post_embeds
from indicators import compute_indicators, derive_signal
from llm import get_llm_read, should_call_llm
from sources.finnhub_src import get_company_news, get_quote
from sources.news_rss import get_news as get_rss_news
from sources.yf_fallback import get_ohlc, get_quote_fallback

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("stockwatch")

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def is_market_hours() -> bool:
    """Return True if current ET time is within regular market hours on a weekday."""
    now = datetime.now(tz=ET)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


# ---------------------------------------------------------------------------
# Per-ticker cycle
# ---------------------------------------------------------------------------

def _article_key(url: str, headline: str) -> str:
    return hashlib.sha256(f"{url}|{headline}".encode()).hexdigest()[:16]


_SUMMARY_DISPLAY = {
    "LEAN_BUY":  ("🟢", "LEAN BUY"),
    "HOLD":      ("⚪", "HOLD"),
    "LEAN_SELL": ("🔴", "LEAN SELL"),
}


def _summary_line(result: dict) -> str:
    emoji, label = _SUMMARY_DISPLAY.get(result["label"], ("⚪", result["label"]))
    change = result["change_pct"]
    sign = "+" if change >= 0 else ""
    return f"{emoji} `{result['ticker']:<6}` {sign}{change:.2f}%  →  {label}"


def process_ticker(ticker: str, name: str, cfg: dict) -> dict | None:
    """Run a full data → indicators → LLM → embed cycle for one ticker.

    Returns a result dict with keys: embed, ticker, change_pct, label.
    Returns None if a fatal error occurred for this ticker.
    """
    settings = cfg["settings"]
    lookback_hours = settings.get("news_lookback_hours", 24)
    model = settings.get("llm_model", "anthropic/claude-haiku-4.5")
    only_new_signal = settings.get("only_call_llm_on_new_signal", True)

    # --- OHLC ---
    try:
        df = store.get_or_refresh_ohlc(ticker, get_ohlc)
    except Exception as exc:
        logger.error("OHLC failed for %s: %s", ticker, exc)
        return None

    # --- Quote ---
    quote = None
    try:
        quote = get_quote(ticker)
    except Exception as exc:
        logger.warning("Finnhub quote failed for %s, trying yfinance: %s", ticker, exc)
        try:
            quote = get_quote_fallback(ticker)
        except Exception as exc2:
            logger.error("All quote sources failed for %s: %s", ticker, exc2)
            return None

    price = quote["price"]

    # --- Indicators ---
    try:
        indicators = compute_indicators(df, price)
        label = derive_signal(indicators, price)
    except Exception as exc:
        logger.error("Indicator computation failed for %s: %s", ticker, exc)
        indicators = {}
        label = "HOLD"

    # --- News ---
    news: list[dict] = []
    new_articles: list[dict] = []  # unseen only (for dedup + LLM gate)

    # Finnhub news
    try:
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        frm_dt = now - timedelta(hours=lookback_hours)
        frm_str = frm_dt.strftime("%Y-%m-%d")
        to_str = now.strftime("%Y-%m-%d")
        finnhub_news = get_company_news(ticker, frm_str, to_str)
        for article in finnhub_news:
            key = _article_key(article["url"], article["headline"])
            article["article_key"] = key
            news.append(article)
    except Exception as exc:
        logger.warning("Finnhub news failed for %s: %s", ticker, exc)

    # Yahoo RSS news (supplemental)
    try:
        rss_news = get_rss_news(ticker)
        existing_urls = {a["url"] for a in news}
        for article in rss_news:
            if article["url"] not in existing_urls:
                news.append(article)
    except Exception as exc:
        logger.warning("RSS news failed for %s: %s", ticker, exc)

    # Dedup and identify new articles
    for article in news:
        key = article.get("article_key") or _article_key(article["url"], article["headline"])
        article["article_key"] = key
        if not store.is_seen(key):
            new_articles.append(article)

    has_new_news = len(new_articles) > 0
    logger.info(
        "%s: price=%.2f label=%s new_news=%d total_news=%d",
        ticker, price, label, len(new_articles), len(news),
    )

    # --- LLM ---
    last_signal = store.get_last_signal(ticker)
    llm_text: str | None = None
    llm_label: str | None = None

    if should_call_llm(ticker, label, last_signal, has_new_news, only_new_signal):
        llm_text, llm_label = get_llm_read(
            ticker, name, quote, indicators, news[:5], model=model
        )
        if llm_text:
            logger.info("%s: LLM label=%s", ticker, llm_label)
    else:
        logger.debug("%s: LLM skipped (no new signal/news)", ticker)

    # --- Build embed ---
    try:
        embed = build_embed(
            ticker=ticker,
            name=name,
            quote=quote,
            indicators=indicators,
            label=label,
            news=news[:5],
            llm_read=llm_text,
            llm_label=llm_label,
        )
    except Exception as exc:
        logger.error("Embed build failed for %s: %s", ticker, exc)
        return None

    # --- Persist state ---
    store.set_last_signal(ticker, label)
    for article in new_articles:
        store.mark_seen(
            article["article_key"],
            ticker,
            article["url"],
            article.get("datetime"),
        )

    return {
        "embed": embed,
        "ticker": ticker,
        "change_pct": quote["change_pct"],
        "label": label,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    cfg = load_config()
    settings = cfg["settings"]

    if settings.get("market_hours_only", True) and not is_market_hours():
        now_et = datetime.now(tz=ET).strftime("%a %Y-%m-%d %H:%M ET")
        logger.info("Outside market hours (%s) — exiting", now_et)
        return

    store.init_db()

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        logger.error("DISCORD_WEBHOOK_URL not set")
        sys.exit(1)

    watchlist = cfg.get("watchlist", [])
    inter_sleep = settings.get("inter_call_sleep_sec", 1.5)
    max_embeds = settings.get("max_embeds_per_post", 10)

    embeds = []
    summary_lines = []

    for i, item in enumerate(watchlist):
        ticker = item["ticker"]
        name = item.get("name", ticker)

        if i > 0:
            time.sleep(inter_sleep)

        try:
            result = process_ticker(ticker, name, cfg)
            if result:
                embeds.append(result["embed"])
                summary_lines.append(_summary_line(result))
        except Exception as exc:
            logger.error("Unexpected error processing %s: %s", ticker, exc)

    # Prepend a deterministic overview embed
    if embeds:
        now_et = datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M ET")
        summary_embed = {
            "title": "📊 StockWatch Summary",
            "description": f"**{now_et}**\n" + "\n".join(summary_lines),
            "color": 0x5865F2,
        }
        embeds.insert(0, summary_embed)

    if embeds:
        try:
            post_embeds(webhook_url, embeds, max_per_post=max_embeds)
            logger.info("Cycle complete — posted %d embed(s)", len(embeds))
        except Exception as exc:
            logger.error("Discord post failed: %s", exc)
    else:
        logger.warning("No embeds to post this cycle")


if __name__ == "__main__":
    main()
