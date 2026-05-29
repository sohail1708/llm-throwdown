"""Free market data: prices + headlines via yfinance."""
from __future__ import annotations

from dataclasses import dataclass

import yfinance as yf

UNIVERSE = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "ORCL"]
STARTING_CASH = 100_000.0


@dataclass
class TickerSnapshot:
    ticker: str
    last_close: float
    pct_1d: float | None
    pct_5d: float | None
    rsi_14: float | None
    headlines: list[str]


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return round(100 - 100 / (1 + rs), 1)


def snapshot(ticker: str) -> TickerSnapshot:
    yf_ticker = yf.Ticker(ticker)
    hist = yf_ticker.history(period="1mo", auto_adjust=False)
    if hist.empty:
        return TickerSnapshot(ticker, 0.0, None, None, None, [])
    closes = [float(c) for c in hist["Close"].tolist()]
    pct_1d = (closes[-1] / closes[-2] - 1) * 100 if len(closes) >= 2 else None
    pct_5d = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else None
    rsi = _rsi(closes)

    headlines: list[str] = []
    try:
        for item in (yf_ticker.news or [])[:3]:
            title = item.get("title") or item.get("content", {}).get("title")
            if title:
                headlines.append(title)
    except Exception:
        pass

    return TickerSnapshot(
        ticker=ticker,
        last_close=round(closes[-1], 2),
        pct_1d=round(pct_1d, 2) if pct_1d is not None else None,
        pct_5d=round(pct_5d, 2) if pct_5d is not None else None,
        rsi_14=rsi,
        headlines=headlines,
    )


def snapshots(tickers: list[str] | None = None) -> list[TickerSnapshot]:
    return [snapshot(t) for t in (tickers or UNIVERSE)]
