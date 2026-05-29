"""Synthetic data with research + tools_used + cost — for dashboard preview."""
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

random.seed(11)
PLAYERS = [
    {"ai": "chatgpt", "label": "ChatGPT", "drift": 0.0015, "vol": 0.012, "avg_tools": 4, "cost_per_day": 0.06},
    {"ai": "claude",  "label": "Claude",  "drift": 0.0010, "vol": 0.010, "avg_tools": 5, "cost_per_day": 0.11},
    {"ai": "gemini",  "label": "Gemini",  "drift": 0.0008, "vol": 0.014, "avg_tools": 3, "cost_per_day": 0.04},
]
TICKERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "ORCL"]
TOOLS = ["get_history", "get_news", "get_financials", "get_analyst_ratings", "read_own_trades"]
ACTIONS = ["BUY", "BUY", "BUY", "HOLD", "SELL"]

BULL_TEMPLATES = [
    "Setup is clean post-pullback, momentum still favors leaders.",
    "Street targets just got bumped on the back of guidance.",
    "Free cash flow trend is improving despite the headwinds.",
    "Pre-earnings drift typically works in this name.",
    "Sentiment turned positive overnight on the supply story.",
]
BEAR_TEMPLATES = [
    "Valuation looks stretched at forward P/E above 35.",
    "Insider selling has picked up over the last two weeks.",
    "Sector breadth is weakening — vulnerable to a rotation.",
    "RSI screaming overbought; near-term pullback risk.",
    "Macro risk is asymmetric here; downside catalyst close.",
]
SUMMARIES = [
    "Tech is mixed; AI infra names look firm, ad-driven names softer.",
    "Defensive lean today — most names look extended, only one clean entry.",
    "Risk-on tape; semis leading, looking for momentum continuation.",
    "Choppy session expected; trimming the weakest looks safer than chasing.",
    "Earnings setup-rich names worth a starter; rest can wait.",
]
REASONS = [
    "Cleanest pullback in the universe, building a starter.",
    "Confluence of street upgrade + clean tape; staking conviction.",
    "Trimming on strength — locked-in winner.",
    "Headlines are noisy but tape is fine; staying out today.",
    "Risk feels asymmetric; cutting exposure.",
]

start = date(2026, 6, 1)
decisions = []
nav_rows = []
nav_by_ai = {p["ai"]: 100_000.0 for p in PLAYERS}
positions_by_ai = {p["ai"]: 0 for p in PLAYERS}
cash_by_ai = {p["ai"]: 100_000.0 for p in PLAYERS}

for i in range(8):
    d = start + timedelta(days=i)
    if d.weekday() >= 5:
        continue
    for p in PLAYERS:
        action = random.choice(ACTIONS)
        ticker = random.choice(TICKERS) if action != "HOLD" else None
        amount = round(random.uniform(5000, 18000), 2) if action == "BUY" else None
        reason = random.choice(REASONS)

        n_candidates = random.choice([2, 3, 3])
        cand_tickers = random.sample(TICKERS, n_candidates)
        candidates = [{
            "ticker": t,
            "bull": random.choice(BULL_TEMPLATES),
            "bear": random.choice(BEAR_TEMPLATES),
            "lean": random.choice(["BUY", "BUY", "HOLD", "SELL"]),
        } for t in cand_tickers]

        n_tools = max(2, min(5, int(random.gauss(p["avg_tools"], 1))))
        tools_used = []
        for _ in range(n_tools):
            tname = random.choice(TOOLS)
            args = {"ticker": random.choice(TICKERS)} if tname != "read_own_trades" else {"ai": p["ai"], "n": 10}
            if tname == "get_history":
                args["days"] = random.choice([15, 30, 60])
            elif tname == "get_news":
                args["n"] = random.choice([3, 5])
            tools_used.append({"name": tname, "args": args})

        cost = round(p["cost_per_day"] * random.uniform(0.7, 1.3), 4)
        decisions.append({
            "date": d.isoformat(),
            "ai": p["ai"],
            "label": p["label"],
            "action": action,
            "ticker": ticker,
            "amount": amount,
            "reason": reason,
            "confidence": random.randint(2, 5),
            "nav_before": round(nav_by_ai[p["ai"]], 2),
            "cash_before": round(cash_by_ai[p["ai"]], 2),
            "research": {
                "candidates": candidates,
                "summary": random.choice(SUMMARIES),
                "preferred_action": action,
                "preferred_ticker": ticker,
                "tools_used": tools_used,
            },
            "tokens": {"input": random.randint(8000, 18000), "output": random.randint(1500, 4000)},
            "cost_usd": cost,
            "order": {"order_id": f"demo-{p['ai']}-{i}", "status": "accepted"},
            "dry_run": False,
            "submitted_at": d.isoformat() + "T13:30:00Z",
        })

        if action == "BUY":
            positions_by_ai[p["ai"]] += 1
            cash_by_ai[p["ai"]] -= amount or 0
        elif action == "SELL":
            positions_by_ai[p["ai"]] = max(0, positions_by_ai[p["ai"]] - 1)

        ret = random.gauss(p["drift"], p["vol"])
        nav_by_ai[p["ai"]] *= 1 + ret
        nav_rows.append({
            "date": d.isoformat(),
            "ai": p["ai"],
            "label": p["label"],
            "nav": round(nav_by_ai[p["ai"]], 2),
            "cash": round(max(cash_by_ai[p["ai"]], 0), 2),
            "positions_value": round(nav_by_ai[p["ai"]] - max(cash_by_ai[p["ai"]], 0), 2),
            "n_positions": positions_by_ai[p["ai"]],
            "positions": [],
        })

(DATA / "decisions.jsonl").write_text("\n".join(json.dumps(d) for d in decisions) + "\n")
(DATA / "nav.jsonl").write_text("\n".join(json.dumps(r) for r in nav_rows) + "\n")
print(f"seeded {len(decisions)} decisions, {len(nav_rows)} nav rows")
for p in PLAYERS:
    spent = sum(d["cost_usd"] for d in decisions if d["ai"] == p["ai"])
    print(f"  {p['label']:8s} final NAV: ${nav_by_ai[p['ai']]:,.2f}  spent: ${spent:.2f}")
