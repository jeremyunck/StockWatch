"""StockWatch CLI entry point."""

import argparse
import sys
import time
from pathlib import Path

from stockwatch.core import DEFAULT_TICKERS, get_quotes, format_table, format_json

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

    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers] if args.tickers else load_config()

    if args.watch:
        watch_mode(tickers, args.interval)
        return

    quotes = get_quotes(tickers)

    if args.json:
        print(format_json(quotes))
    else:
        print(format_table(quotes))


if __name__ == "__main__":
    main()