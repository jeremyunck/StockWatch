"""StockWatch CLI entry point."""

import argparse
import sys
import time
from pathlib import Path

from stockwatch.core import DEFAULT_TICKERS, get_quotes, format_table, format_json
from stockwatch.reddit import (
    get_reddit_client,
    fetch_reddit_sentiment,
    summarize_with_llm,
    format_watchlist_summary,
)

try:
    import tomli
except ImportError:
    tomli = None


def load_config() -> list[str]:
    """Load tickers from TOML config file."""
    config_paths = [
        Path.home() / ".stockwatch.toml",
        Path.home() / ".config" / "stockwatch" / "config.toml",
    ]

    for path in config_paths:
        if path.exists() and tomli:
            try:
                data = tomli.loads(path.read_text())
                tickers = data.get("tickers", [])
                if tickers:
                    return [t.strip().upper() for t in tickers if t.strip()]
            except Exception:
                pass

    return DEFAULT_TICKERS


def watch_mode(tickers: list[str], interval: int):
    """Continuously watch tickers."""
    try:
        while True:
            quotes = get_quotes(tickers)
            print("\033c", end="")  # Clear screen
            print(format_table(quotes))
            print(f"Refreshing every {interval}s (Ctrl+C to stop)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")


def main():
    parser = argparse.ArgumentParser(
        description="StockWatch - Track stock prices and technical indicators"
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        help="Tickers to check (defaults to config or built-in list)",
    )
    parser.add_argument(
        "--watch", "-w",
        action="store_true",
        help="Watch mode - refresh continuously",
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=60,
        help="Refresh interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--reddit", "-r",
        action="store_true",
        help="Include Reddit sentiment analysis (requires REDDIT_CLIENT_ID/SECRET env vars)",
    )
    parser.add_argument(
        "--openrouter-key",
        type=str,
        default=os.environ.get("OPENROUTER_API_KEY", ""),
        help="OpenRouter API key for LLM sentiment summary",
    )

    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers] if args.tickers else load_config()

    if args.watch:
        watch_mode(tickers, args.interval)
        return

    quotes = get_quotes(tickers)

    if args.reddit:
        if not args.openrouter_key:
            print("Error: --openrouter-key or OPENROUTER_API_KEY env var required for Reddit sentiment", file=sys.stderr)
            sys.exit(1)
        try:
            reddit = get_reddit_client()
        except Exception as e:
            print(f"Error initializing Reddit client: {e}", file=sys.stderr)
            sys.exit(1)

        reddit_results = {}
        summaries = {}
        for q in quotes:
            print(f"Fetching Reddit sentiment for {q.ticker}...", file=sys.stderr)
            rr = fetch_reddit_sentiment(reddit, q.ticker)
            reddit_results[q.ticker] = rr
            summaries[q.ticker] = summarize_with_llm(
                q.ticker, q.price, q.change_pct, rr, args.openrouter_key
            )

        if args.json:
            # Augment JSON with Reddit data
            import json
            from stockwatch.core import asdict
            out = [asdict(q) for q in quotes]
            for item in out:
                item["reddit"] = asdict(reddit_results.get(item["ticker"], {}))
                item["reddit"]["llm_summary"] = summaries.get(item["ticker"], "")
            print(json.dumps(out, indent=2, default=str))
        else:
            print(format_watchlist_summary(quotes, reddit_results, summaries))
        return

    if args.json:
        print(format_json(quotes))
    else:
        print(format_table(quotes))


if __name__ == "__main__":
    main()