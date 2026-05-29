"""Shared prompts for the 2-phase pipeline (research → decision)."""
from __future__ import annotations

from datetime import date

from src.data import TickerSnapshot

RESEARCH_SYSTEM_PROMPT = """You are an opinionated portfolio manager competing in a 30-day paper-trading showdown against two other AIs.

You start with $100,000 and pick ONE move per trading day. Your goal: highest portfolio return at the end of the month.

UNIVERSE (long only): AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, AVGO, ORCL.

This is the RESEARCH phase. Investigate the market, then propose 2-3 candidate setups for today.

You have tools (use up to 5 calls total — be selective, not exhaustive):
- get_history(ticker, days): OHLCV history
- get_news(ticker, n): recent headlines
- get_financials(ticker): P/E, growth, margins, debt
- get_analyst_ratings(ticker): Street targets and buy/sell distribution
- read_own_trades(ai, n): YOUR OWN past trades and outcomes — pass your slug ({ai_slug}) as `ai`

Pick the most interesting 2-3 setups, dig deeper on those, then output a structured analysis.

When you're done researching, respond with ONLY this JSON (no prose, no codefences):
{
  "candidates": [
    {"ticker": "AAPL", "bull": "<one sentence>", "bear": "<one sentence>", "lean": "BUY"|"SELL"|"HOLD"},
    ...
  ],
  "summary": "<one-sentence read of today's setup>",
  "preferred_action": "BUY"|"SELL"|"HOLD",
  "preferred_ticker": "<ticker>"|null
}"""


DECISION_SYSTEM_PROMPT = """You are the same portfolio manager. You've just finished researching.

Now commit to exactly ONE move for today. Be decisive and concise.

Rules:
- BUY: min $5,000 notional, max 25% of NAV per position.
- SELL: closes the FULL current position (no partial sells).
- HOLD: no action today.

Output STRICT JSON only, no prose, no codefences:
{"action": "BUY"|"SELL"|"HOLD", "ticker": "<from universe>"|null, "amount": <number>|null, "reason": "<≤30 words, casual trader voice>", "confidence": 1-5}

- For HOLD: ticker=null, amount=null.
- For SELL: amount=null (we close the full position).
- For BUY: amount is the dollar notional to deploy."""


def build_research_context(
    snaps: list[TickerSnapshot],
    *,
    ai_slug: str,
    today: str,
    nav: float,
    cash: float,
    positions: list[dict],
) -> str:
    """Build the initial user message for the research phase."""
    lines = [
        f"Today: {today}",
        f"You are: {ai_slug}",
        "",
        f"YOUR PORTFOLIO  NAV ${nav:,.2f}  cash ${cash:,.2f}",
    ]
    if positions:
        for p in positions:
            lines.append(
                f"  {p['ticker']:6s} ${p['market_value']:>9,.2f}  "
                f"({p['qty']:.3f} sh @ avg ${p['avg_entry_price']:.2f})"
            )
    else:
        lines.append("  (no open positions)")

    lines.append("")
    lines.append("MARKET SNAPSHOT (yesterday's close, RSI 14, 1d %, 5d %):")
    for s in snaps:
        lines.append(
            f"  {s.ticker:6s} ${s.last_close:>8.2f}  RSI={s.rsi_14}  "
            f"1d={s.pct_1d}%  5d={s.pct_5d}%"
        )

    lines.append("")
    lines.append("Investigate with tools (≤5 calls), then output the structured JSON.")
    return "\n".join(lines)


def build_decision_context(research_json_text: str, *, nav: float, cash: float, positions: list[dict]) -> str:
    """Build the user message for the decision phase."""
    lines = ["YOUR RESEARCH OUTPUT:"]
    lines.append(research_json_text)
    lines.append("")
    lines.append(f"PORTFOLIO  NAV ${nav:,.2f}  cash ${cash:,.2f}")
    if positions:
        for p in positions:
            lines.append(f"  {p['ticker']:6s} ${p['market_value']:>9,.2f}")
    else:
        lines.append("  (no open positions)")
    lines.append("")
    lines.append("Commit to ONE move. JSON only.")
    return "\n".join(lines)


def today_iso() -> str:
    return date.today().isoformat()
