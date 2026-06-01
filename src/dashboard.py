"""Render docs/index.html — leaderboard + research + cost view."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.data import STARTING_CASH
from src.providers import PROVIDERS

ROOT = Path(__file__).parent.parent
TEMPLATE_DIR = ROOT / "templates"
OUT_HTML = ROOT / "docs" / "index.html"
OUT_DATA = ROOT / "docs" / "data.json"

LAUNCH_DATE = date(2026, 6, 1)
END_DATE = date(2026, 7, 14)
TOTAL_DAYS = 30
BUDGET_PER_AI = 10.0

BENCHMARK_SLUG = "qqq"
BENCHMARK_LABEL = "QQQ"
BENCHMARK_ACCENT = "zinc"


def _day_label(today: date) -> dict:
    if today < LAUNCH_DATE:
        gap = (LAUNCH_DATE - today).days
        return {
            "label": "Eve" if gap == 1 else f"T-{gap}",
            "sub": "Day 1 starts Mon Jun 1" if gap > 1 else "Day 1 starts tomorrow",
        }
    if today > END_DATE:
        return {"label": "Final", "sub": "30-day showdown complete"}
    n = 0
    d = LAUNCH_DATE
    while d <= today:
        if d.weekday() < 5:
            n += 1
        d = date.fromordinal(d.toordinal() + 1)
    return {"label": f"Day {n}", "sub": f"{n} of {TOTAL_DAYS}"}


def _series_per_ai(nav_history: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in nav_history:
        grouped[r["ai"]].append(r)
    for ai in grouped:
        grouped[ai].sort(key=lambda r: r["date"])
    return grouped


def _total_cost_per_ai(decisions: list[dict]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for d in decisions:
        out[d["ai"]] += float(d.get("cost_usd") or 0)
    return out


def _position_metrics(latest: dict | None) -> tuple[float, float, float, list[dict]]:
    """Return (positions_value, cost_basis, position_return_pct, enriched_positions).

    Position return is computed against cost basis (qty * avg_entry_price) so
    uninvested cash doesn't dilute the read on stock-picking skill. If no
    positions, returns zeros — caller decides how to display.
    """
    if not latest or not latest.get("positions"):
        return 0.0, 0.0, 0.0, []
    positions_value = 0.0
    cost_basis = 0.0
    enriched: list[dict] = []
    for pos in latest["positions"]:
        mv = float(pos.get("market_value") or 0)
        qty = float(pos.get("qty") or 0)
        entry = float(pos.get("avg_entry_price") or 0)
        cur_price = float(pos.get("current_price") or (mv / qty if qty else 0))
        pos_cost = qty * entry
        positions_value += mv
        cost_basis += pos_cost
        pos_ret_pct = (mv / pos_cost - 1) * 100 if pos_cost else 0.0
        enriched.append({
            "ticker": pos["ticker"],
            "qty": qty,
            "avg_entry_price": round(entry, 2),
            "current_price": round(cur_price, 2),
            "market_value": round(mv, 2),
            "cost_basis": round(pos_cost, 2),
            "pl_usd": round(mv - pos_cost, 2),
            "pl_pct": round(pos_ret_pct, 2),
        })
    enriched.sort(key=lambda x: x["market_value"], reverse=True)
    ret_pct = (positions_value / cost_basis - 1) * 100 if cost_basis else 0.0
    return positions_value, cost_basis, ret_pct, enriched


def _leaderboard(nav_history: list[dict], cost_by_ai: dict[str, float]) -> list[dict]:
    series = _series_per_ai(nav_history)
    rows = []
    for p in PROVIDERS:
        history = series.get(p.name) or []
        latest = history[-1] if history else None
        portfolio_value, cost_basis, ret_pct, positions = _position_metrics(latest)
        spent = cost_by_ai.get(p.name, 0.0)
        cash = float(latest["cash"]) if latest else STARTING_CASH
        days_first = 0
        # Rank "leader of the day" by position return %, not by total account.
        all_dates = sorted({r["date"] for r in nav_history if r.get("ai") != BENCHMARK_SLUG})
        for d in all_dates:
            day_returns = {}
            for r in nav_history:
                if r["date"] != d or r.get("ai") == BENCHMARK_SLUG:
                    continue
                _pv, _cb, _rp, _ = _position_metrics(r)
                if _cb > 0:
                    day_returns[r["ai"]] = _rp
            if day_returns and max(day_returns.values()) == day_returns.get(p.name, float("-inf")):
                days_first += 1
        rows.append({
            "name": p.name,
            "label": p.label,
            "accent": p.accent,
            "portfolio_value": round(portfolio_value, 2),
            "cost_basis": round(cost_basis, 2),
            "ret_pct": round(ret_pct, 2),
            "days_first": days_first,
            "n_positions": len(positions),
            "cash": round(cash, 2),
            "spent_usd": round(spent, 2),
            "budget_usd": BUDGET_PER_AI,
            "budget_pct": min(100, round(spent / BUDGET_PER_AI * 100, 1)),
            "positions": positions,
        })
    rows.sort(key=lambda r: r["ret_pct"], reverse=True)
    for rank, r in enumerate(rows, start=1):
        r["rank"] = rank
    return rows


def _today_decisions(decisions: list[dict]) -> dict[str, dict]:
    if not decisions:
        return {}
    latest_date = max(d["date"] for d in decisions)
    out: dict[str, dict] = {}
    for d in decisions:
        if d["date"] == latest_date:
            out[d["ai"]] = d
    return out


def _enrich_decisions_with_fills(decisions: list[dict], nav_history: list[dict]) -> list[dict]:
    """For each BUY decision, look up the fill price from the first subsequent
    nav.jsonl snapshot that contains that position for that AI.
    """
    by_ai_date = defaultdict(list)
    for r in nav_history:
        if r.get("ai") == BENCHMARK_SLUG:
            continue
        by_ai_date[r["ai"]].append(r)
    for ai in by_ai_date:
        by_ai_date[ai].sort(key=lambda r: r["date"])

    enriched: list[dict] = []
    for d in decisions:
        out = dict(d)
        if d.get("action") == "BUY" and d.get("ticker"):
            for nav_row in by_ai_date.get(d["ai"], []):
                if nav_row["date"] < d["date"]:
                    continue
                for pos in nav_row.get("positions", []):
                    if pos["ticker"] == d["ticker"] and pos.get("avg_entry_price"):
                        out["fill_price"] = pos["avg_entry_price"]
                        break
                if "fill_price" in out:
                    break
        enriched.append(out)
    return enriched


def _tool_usage_per_ai(decisions: list[dict]) -> dict[str, dict[str, int]]:
    """Total calls per tool per AI across the whole run."""
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for d in decisions:
        research = d.get("research")
        if not research:
            continue
        for t in research.get("tools_used", []):
            out[d["ai"]][t["name"]] += 1
    return {ai: dict(stats) for ai, stats in out.items()}


def _benchmark_card(nav_history: list[dict], leaderboard: list[dict]) -> dict | None:
    """Build the QQQ benchmark card. Returns None if no QQQ data yet.

    Comparison is return-% based: how many AIs have a position-level return
    that beats QQQ's return. Absolute NAVs are not comparable since AI NAV
    here excludes cash.
    """
    bench_rows = [r for r in nav_history if r.get("ai") == BENCHMARK_SLUG]
    if not bench_rows:
        return None
    bench_rows.sort(key=lambda r: r["date"])
    latest = bench_rows[-1]
    nav = latest["nav"]
    ret_pct = (nav / STARTING_CASH - 1) * 100
    leaders_beating = sum(1 for r in leaderboard if r["ret_pct"] > ret_pct)
    return {
        "label": BENCHMARK_LABEL,
        "accent": BENCHMARK_ACCENT,
        "nav": round(nav, 2),
        "ret_pct": round(ret_pct, 2),
        "ais_ahead": leaders_beating,
        "ais_total": len(leaderboard),
    }


def render_dashboard(*, nav_history: list[dict], decisions: list[dict]) -> None:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    tpl = env.get_template("index.html")

    ai_nav_history = [r for r in nav_history if r.get("ai") != BENCHMARK_SLUG]

    cost_by_ai = _total_cost_per_ai(decisions)
    leaderboard = _leaderboard(ai_nav_history, cost_by_ai)
    today_decisions = _today_decisions(decisions)
    series_per_ai = _series_per_ai(ai_nav_history)
    tool_usage = _tool_usage_per_ai(decisions)
    benchmark = _benchmark_card(nav_history, leaderboard)

    chart_series = []
    for p in PROVIDERS:
        hist = series_per_ai.get(p.name) or []
        points = []
        for r in hist:
            pv, cb, rp, _ = _position_metrics(r)
            # No positions yet → flat at 0% (vs reporting NaN). Once deployed,
            # ret_pct is the position-level return so the line is comparable
            # to the QQQ benchmark.
            points.append({
                "date": r["date"],
                "ret_pct": round(rp if cb else 0.0, 2),
                "portfolio_value": round(pv, 2),
            })
        chart_series.append({
            "name": p.name,
            "label": p.label,
            "accent": p.accent,
            "points": points,
            "is_benchmark": False,
        })
    bench_hist = sorted(
        [r for r in nav_history if r.get("ai") == BENCHMARK_SLUG],
        key=lambda r: r["date"],
    )
    if bench_hist:
        bench_points = []
        for r in bench_hist:
            bench_points.append({
                "date": r["date"],
                "ret_pct": round((r["nav"] / STARTING_CASH - 1) * 100, 2),
                "portfolio_value": round(r["nav"], 2),
            })
        chart_series.append({
            "name": BENCHMARK_SLUG,
            "label": BENCHMARK_LABEL,
            "accent": BENCHMARK_ACCENT,
            "points": bench_points,
            "is_benchmark": True,
        })

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    html = tpl.render(
        day=_day_label(date.today()),
        leaderboard=leaderboard,
        benchmark=benchmark,
        today_decisions=[today_decisions.get(p.name) for p in PROVIDERS],
        providers=[{"name": p.name, "label": p.label, "accent": p.accent} for p in PROVIDERS],
        chart_series=chart_series,
        decisions=list(reversed(_enrich_decisions_with_fills(decisions, nav_history)))[:30],
        tool_usage=[{"ai": p.name, "label": p.label, "accent": p.accent, "counts": tool_usage.get(p.name, {})} for p in PROVIDERS],
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        starting_cash=STARTING_CASH,
        budget_per_ai=BUDGET_PER_AI,
    )
    OUT_HTML.write_text(html)
    OUT_DATA.write_text(json.dumps({
        "leaderboard": leaderboard,
        "today_decisions": today_decisions,
        "chart_series": chart_series,
        "tool_usage": tool_usage,
        "total_cost_per_ai": cost_by_ai,
    }, default=str, indent=2))
