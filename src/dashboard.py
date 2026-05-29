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


def _leaderboard(nav_history: list[dict], cost_by_ai: dict[str, float]) -> list[dict]:
    series = _series_per_ai(nav_history)
    rows = []
    all_dates = sorted({r["date"] for r in nav_history})
    for p in PROVIDERS:
        history = series.get(p.name) or []
        latest = history[-1] if history else None
        nav = latest["nav"] if latest else STARTING_CASH
        ret_pct = (nav / STARTING_CASH - 1) * 100
        days_first = 0
        for d in all_dates:
            navs = {r["ai"]: r["nav"] for r in nav_history if r["date"] == d}
            if navs and max(navs.values()) == navs.get(p.name, 0):
                days_first += 1
        spent = cost_by_ai.get(p.name, 0.0)
        rows.append({
            "name": p.name,
            "label": p.label,
            "accent": p.accent,
            "nav": round(nav, 2),
            "ret_pct": round(ret_pct, 2),
            "days_first": days_first,
            "n_positions": latest["n_positions"] if latest else 0,
            "cash": latest["cash"] if latest else STARTING_CASH,
            "spent_usd": round(spent, 2),
            "budget_usd": BUDGET_PER_AI,
            "budget_pct": min(100, round(spent / BUDGET_PER_AI * 100, 1)),
        })
    rows.sort(key=lambda r: r["nav"], reverse=True)
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


def render_dashboard(*, nav_history: list[dict], decisions: list[dict]) -> None:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    tpl = env.get_template("index.html")

    cost_by_ai = _total_cost_per_ai(decisions)
    leaderboard = _leaderboard(nav_history, cost_by_ai)
    today_decisions = _today_decisions(decisions)
    series_per_ai = _series_per_ai(nav_history)
    tool_usage = _tool_usage_per_ai(decisions)

    chart_series = []
    for p in PROVIDERS:
        hist = series_per_ai.get(p.name) or []
        chart_series.append({
            "name": p.name,
            "label": p.label,
            "accent": p.accent,
            "points": [{"date": r["date"], "nav": r["nav"]} for r in hist],
        })

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    html = tpl.render(
        day=_day_label(date.today()),
        leaderboard=leaderboard,
        today_decisions=[today_decisions.get(p.name) for p in PROVIDERS],
        providers=[{"name": p.name, "label": p.label, "accent": p.accent} for p in PROVIDERS],
        chart_series=chart_series,
        decisions=list(reversed(decisions))[:30],
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
