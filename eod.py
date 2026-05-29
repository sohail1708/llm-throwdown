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
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.broker import Trader
from src.dashboard import render_dashboard
from src.providers import PROVIDERS

NAV_PATH = Path(__file__).parent / "data" / "nav.jsonl"
DECISIONS_PATH = Path(__file__).parent / "data" / "decisions.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def snapshot() -> int:
    today = date.today().isoformat()
    NAV_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for provider in PROVIDERS:
        try:
            trader = Trader(provider.alpaca_key_env, provider.alpaca_secret_env)
            acct = trader.account()
            positions = trader.positions()
            row = {
                "date": today,
                "ai": provider.name,
                "label": provider.label,
                "nav": round(acct.portfolio_value, 2),
                "cash": round(acct.cash, 2),
                "positions_value": round(acct.portfolio_value - acct.cash, 2),
                "n_positions": len(positions),
                "positions": [
                    {"ticker": p.ticker, "qty": round(p.qty, 4), "market_value": round(p.market_value, 2)}
                    for p in positions
                ],
            }
            print(f"  {provider.label:10s} NAV ${row['nav']:>10,.2f}  cash ${row['cash']:>10,.2f}  positions={row['n_positions']}")
            rows.append(row)
        except Exception as e:
            print(f"  {provider.label:10s} ERROR: {e!r}")

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
