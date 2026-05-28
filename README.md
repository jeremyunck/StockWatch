# StockWatch

Your friendly stock watcher. 📈

Track stock prices and technical indicators from the command line.

## Installation

```bash
pip install -e .
```

## Usage

```bash
# Check default tickers
stockwatch

# Check specific tickers
stockwatch AAPL MSFT GOOGL

# Watch mode (refresh every 60s)
stockwatch --watch

# Custom interval
stockwatch --watch --interval 30
```

## Configuration

Create `~/.stockwatch.toml` or `~/.config/stockwatch/config.toml`:

```toml
tickers = [AAPL, MSFT, GOOGL, AMZN, NVDA]
```

