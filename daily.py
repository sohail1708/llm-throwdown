"""Morning fan-out: each contestant runs research → decision → execute.

Each AI does its own tool-using research, commits to ONE move, and the
broker submits it on that AI's Alpaca paper account. Decisions (including
the full research artifact + tool usage + token cost) are appended to
data/decisions.jsonl.

CLI:
    python daily.py              # live: real Alpaca orders, real LLM calls
    python daily.py --dry-run    # run the pipeline; skip the Alpaca submit
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src import data as md
from src.broker import Trader
from src.providers import PROVIDERS, Provider

DECISIONS_PATH = Path(__file__).parent / "data" / "decisions.jsonl"


def _portfolio_state(trader: Trader) -> dict:
    acct = trader.account()
    positions = trader.positions()
    return {
        "nav": acct.portfolio_value,
        "cash": acct.cash,
        "positions": [
            {
                "ticker": p.ticker,
                "qty": p.qty,
                "avg_entry_price": p.avg_entry_price,
                "market_value": p.market_value,
            }
            for p in positions
        ],
    }


def _execute(trader: Trader, decision) -> dict | None:
    if decision.action == "HOLD":
        return None
    if decision.action == "BUY":
        o = trader.buy(decision.ticker, decision.amount)
    elif decision.action == "SELL":
        o = trader.close_position(decision.ticker)
    else:
        return None
    return {"order_id": o.id, "status": o.status}


def _research_to_dict(research) -> dict:
    return {
        "candidates": [asdict(c) for c in research.candidates],
        "summary": research.summary,
        "preferred_action": research.preferred_action,
        "preferred_ticker": research.preferred_ticker,
        "tools_used": research.tools_used,
    }


def run_one(provider: Provider, snaps, *, dry_run: bool) -> dict:
    print(f"\n=== {provider.label} ===")
    trader = Trader(provider.alpaca_key_env, provider.alpaca_secret_env)
    state = _portfolio_state(trader)
    print(f"  NAV ${state['nav']:,.2f}  cash ${state['cash']:,.2f}  positions={len(state['positions'])}")

    try:
        result = provider.run(
            snaps=snaps, nav=state["nav"], cash=state["cash"], positions=state["positions"]
        )
    except Exception as e:
        print(f"  ✗ pipeline failed: {e!r}")
        traceback.print_exc()
        return {
            "date": date.today().isoformat(),
            "ai": provider.name,
            "label": provider.label,
            "action": "ERROR",
            "ticker": None,
            "amount": None,
            "reason": f"agent error: {e!s}"[:300],
            "confidence": None,
            "nav_before": state["nav"],
            "cash_before": state["cash"],
            "research": None,
            "tokens": None,
            "cost_usd": 0.0,
            "order": None,
            "dry_run": dry_run,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }

    research = result["research"]
    decision = result["decision"]
    tokens = result["tokens"]
    cost = result["cost_usd"]

    print(f"  research: {len(research.candidates)} candidates, {len(research.tools_used)} tool calls")
    for c in research.candidates:
        print(f"    {c.ticker:6s} lean={c.lean}")
    print(f"  → {decision.action} {decision.ticker or ''} "
          f"{('$' + format(decision.amount, ',.0f')) if decision.amount else ''}".rstrip())
    print(f'    "{decision.reason}"  (conf {decision.confidence}/5)')
    print(f"  tokens in={tokens['input']} out={tokens['output']}  cost=${cost:.4f}")

    order = None
    if not dry_run:
        try:
            order = _execute(trader, decision)
            if order:
                print(f"  submitted order_id={order['order_id']} status={order['status']}")
        except Exception as e:
            print(f"  ✗ execute failed: {e!r}")
            order = {"error": str(e)[:200]}

    return {
        "date": date.today().isoformat(),
        "ai": provider.name,
        "label": provider.label,
        "action": decision.action,
        "ticker": decision.ticker,
        "amount": decision.amount,
        "reason": decision.reason,
        "confidence": decision.confidence,
        "nav_before": state["nav"],
        "cash_before": state["cash"],
        "research": _research_to_dict(research),
        "tokens": tokens,
        "cost_usd": cost,
        "order": order,
        "dry_run": dry_run,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }


def run(*, dry_run: bool = False) -> int:
    today = date.today()
    print(f"[daily] {today.isoformat()}  dry_run={dry_run}")
    print("[daily] fetching market snapshot...")
    snaps = md.snapshots()
    for s in snaps:
        print(f"  {s.ticker:6s} ${s.last_close:>8.2f}  RSI={s.rsi_14}  1d={s.pct_1d}%  5d={s.pct_5d}%")

    DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    total_cost = 0.0
    for provider in PROVIDERS:
        row = run_one(provider, snaps, dry_run=dry_run)
        rows.append(row)
        total_cost += row.get("cost_usd") or 0

    with DECISIONS_PATH.open("a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print(f"\n[daily] logged {len(rows)} decisions to {DECISIONS_PATH}")
    print(f"[daily] total LLM cost today: ${total_cost:.4f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Daily 3-LLM research + decision + execute.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
