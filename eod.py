"""End-of-day NAV snapshot + dashboard rebuild.

Runs once per weekday a few minutes after US close. For each contestant:
fetches their Alpaca paper account NAV + open positions and appends to
data/nav.jsonl. Then regenerates docs/index.html from all NAV history.

CLI:
    python eod.py                  # live snapshot + rebuild
    python eod.py --rebuild-only   # skip Alpaca; just regen the dashboard
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv

from src.broker import Trader
from src.dashboard import LAUNCH_DATE, render_dashboard
from src.data import STARTING_CASH
from src.providers import PROVIDERS

NAV_PATH = Path(__file__).parent / "data" / "nav.jsonl"
DECISIONS_PATH = Path(__file__).parent / "data" / "decisions.jsonl"

BENCHMARK_TICKER = "QQQ"
BENCHMARK_SLUG = "qqq"
BENCHMARK_LABEL = "QQQ"


def _yf_close(ticker: str) -> float | None:
    """Today's consolidated NMS close from yfinance. Returns None on miss.

    We use this for all position-level pricing so the dashboard shows the
    SAME close that Yahoo / Google Finance / Schwab show, not Alpaca's
    IEX-only paper feed (which can be minutes stale and off by several
    dollars on illiquid moments).
    """
    try:
        hist = yf.Ticker(ticker).history(period="2d", auto_adjust=False)
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _qqq_snapshot() -> dict | None:
    """Compute QQQ benchmark NAV: $100k entered at LAUNCH_DATE open.

    Before launch day → flat at $100k. From launch day onward → mark to
    market using QQQ's close vs the open price on the first trading day at
    or after LAUNCH_DATE.
    """
    today = date.today()
    try:
        t = yf.Ticker(BENCHMARK_TICKER)
        hist = t.history(
            start=(LAUNCH_DATE - timedelta(days=5)).isoformat(),
            end=(today + timedelta(days=1)).isoformat(),
            auto_adjust=False,
        )
        if hist.empty:
            print(f"  {BENCHMARK_LABEL:10s} no data")
            return None

        post_launch = hist[hist.index.date >= LAUNCH_DATE]
        if post_launch.empty:
            nav = STARTING_CASH
            entry_price = None
            shares = 0.0
        else:
            entry_price = float(post_launch.iloc[0]["Open"])
            shares = STARTING_CASH / entry_price
            today_price = float(hist.iloc[-1]["Close"])
            nav = shares * today_price

        positions_value = nav if entry_price is not None else 0.0
        cash = 0.0 if entry_price is not None else STARTING_CASH
        row = {
            "date": today.isoformat(),
            "ai": BENCHMARK_SLUG,
            "label": BENCHMARK_LABEL,
            "nav": round(nav, 2),
            "cash": round(cash, 2),
            "positions_value": round(positions_value, 2),
            "n_positions": 1 if entry_price else 0,
            "positions": (
                [{
                    "ticker": BENCHMARK_TICKER,
                    "qty": round(shares, 4),
                    "market_value": round(positions_value, 2),
                }]
                if entry_price else []
            ),
            "is_benchmark": True,
        }
        print(f"  {BENCHMARK_LABEL:10s} NAV ${row['nav']:>10,.2f}  (entry ${entry_price or 0:.2f})")
        return row
    except Exception as e:
        print(f"  {BENCHMARK_LABEL:10s} ERROR: {e!r}")
        return None


def snapshot() -> int:
    today = date.today().isoformat()
    NAV_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for provider in PROVIDERS:
        try:
            trader = Trader(provider.alpaca_key_env, provider.alpaca_secret_env)
            acct = trader.account()
            positions = trader.positions()

            # Override Alpaca's market_value with yfinance close so the dashboard
            # matches the consolidated tape that the rest of the world sees.
            positions_data: list[dict] = []
            positions_value = 0.0
            for p in positions:
                yf_close = _yf_close(p.ticker)
                cur_price = yf_close if yf_close is not None else (p.market_value / p.qty if p.qty else 0)
                mv = p.qty * cur_price
                positions_value += mv
                positions_data.append({
                    "ticker": p.ticker,
                    "qty": round(p.qty, 4),
                    "avg_entry_price": round(p.avg_entry_price, 4),
                    "market_value": round(mv, 2),
                    "current_price": round(cur_price, 4),
                    "unrealized_pl": round(mv - p.qty * p.avg_entry_price, 2),
                    "price_source": "yfinance" if yf_close is not None else "alpaca",
                })

            nav = acct.cash + positions_value
            row = {
                "date": today,
                "ai": provider.name,
                "label": provider.label,
                "nav": round(nav, 2),
                "cash": round(acct.cash, 2),
                "positions_value": round(positions_value, 2),
                "n_positions": len(positions),
                "positions": positions_data,
            }
            print(f"  {provider.label:10s} NAV ${row['nav']:>10,.2f}  cash ${row['cash']:>10,.2f}  positions={row['n_positions']}")
            rows.append(row)
        except Exception as e:
            print(f"  {provider.label:10s} ERROR: {e!r}")

    qqq_row = _qqq_snapshot()
    if qqq_row is not None:
        rows.append(qqq_row)

    with NAV_PATH.open("a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print(f"[eod] appended {len(rows)} NAV rows to {NAV_PATH}")
    return 0


def rebuild() -> int:
    nav_history = _load_jsonl(NAV_PATH)
    decisions = _load_jsonl(DECISIONS_PATH)
    render_dashboard(nav_history=nav_history, decisions=decisions)
    print(f"[eod] rebuilt dashboard ({len(nav_history)} nav rows, {len(decisions)} decisions)")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="EOD NAV snapshot + dashboard rebuild.")
    parser.add_argument("--rebuild-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.rebuild_only:
        snapshot()
    return rebuild()


if __name__ == "__main__":
    sys.exit(main())
