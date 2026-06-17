"""Render docs/index.html — leaderboard + research + cost view."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.data import STARTING_CASH, UNIVERSE
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
        # TRUE total return — account equity (cash + positions) vs starting
        # capital. This is the honest "did the AI make money since Jun 1"
        # number. It captures realized losses from sells (which only show up
        # as reduced cash) AND any margin abuse (negative cash drags the
        # equity down). The earlier "position-level return" was misleading:
        # it ignored cash drag, ignored realized P/L, and made a leveraged
        # AI look like a better picker than it was.
        total_equity = round(cash + portfolio_value, 2)
        total_ret_pct = round((total_equity / STARTING_CASH - 1) * 100, 2)
        rows.append({
            "name": p.name,
            "label": p.label,
            "accent": p.accent,
            # Hero metric: true return on $100k starting capital.
            "total_equity": total_equity,
            "total_ret_pct": total_ret_pct,
            # Secondary: position-level pick quality (open positions only).
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
            # Margin flag: cash < 0 means the AI bought beyond its means.
            "is_leveraged": cash < -1.0,
        })
    # Rank by TRUE total return (the experiment's actual scoreboard).
    rows.sort(key=lambda r: r["total_ret_pct"], reverse=True)
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


def _journal(decisions: list[dict], nav_history: list[dict]) -> list[dict]:
    """Build a per-AI narrative timeline. One entry per AI per day that has
    *either* a decision or a NAV snapshot, in chronological order.

    Each entry tells the story of that day: what the AI saw, what it decided,
    why, and how that played out by EOD (NAV change, cumulative return,
    QQQ comparison).
    """
    enriched_decisions = _enrich_decisions_with_fills(decisions, nav_history)
    decisions_by_ai_date: dict[tuple[str, str], dict] = {
        (d["ai"], d["date"]): d for d in enriched_decisions
    }

    # NAV history per AI, deduped by date (latest wins per date), sorted.
    nav_by_ai: dict[str, list[dict]] = defaultdict(list)
    for r in nav_history:
        if r.get("ai") == BENCHMARK_SLUG:
            continue
        nav_by_ai[r["ai"]].append(r)
    for ai in nav_by_ai:
        seen: set[str] = set()
        dedup = []
        for r in sorted(nav_by_ai[ai], key=lambda x: x["date"]):
            if r["date"] in seen:
                continue
            seen.add(r["date"])
            dedup.append(r)
        nav_by_ai[ai] = dedup

    # QQQ rows keyed by date for the benchmark column.
    qqq_by_date = {r["date"]: r for r in nav_history if r.get("ai") == BENCHMARK_SLUG}

    journal: list[dict] = []
    for p in PROVIDERS:
        nav_rows = nav_by_ai.get(p.name, [])
        # Universe of dates this AI has data for (either decided or snapped).
        date_set = {r["date"] for r in nav_rows}
        date_set.update(d["date"] for d in enriched_decisions if d["ai"] == p.name)
        dates = sorted(date_set)

        days: list[dict] = []
        prev_nav = STARTING_CASH
        for day_num, dt in enumerate(dates, start=1):
            decision = decisions_by_ai_date.get((p.name, dt))
            nav_row = next((r for r in nav_rows if r["date"] == dt), None)
            nav_eod = float(nav_row["nav"]) if nav_row else None
            day_change_pct = None
            if nav_eod is not None:
                day_change_pct = (nav_eod / prev_nav - 1) * 100 if prev_nav else 0
                prev_nav = nav_eod
            cumulative_ret_pct = (nav_eod / STARTING_CASH - 1) * 100 if nav_eod is not None else None

            # Position-level return at EOD (vs cost basis)
            position_ret_pct = None
            if nav_row:
                pv, cb, rp, _ = _position_metrics(nav_row)
                if cb:
                    position_ret_pct = rp

            qqq_row = qqq_by_date.get(dt)
            qqq_ret_pct = ((qqq_row["nav"] / STARTING_CASH) - 1) * 100 if qqq_row else None

            research = (decision or {}).get("research") or {}
            tools_used = research.get("tools_used") or []
            day = {
                "date": dt,
                "day_num": day_num,
                "has_decision": decision is not None,
                "action": decision.get("action") if decision else None,
                "ticker": decision.get("ticker") if decision else None,
                "amount": decision.get("amount") if decision else None,
                "fill_price": decision.get("fill_price") if decision else None,
                "reason": decision.get("reason") if decision else None,
                "confidence": decision.get("confidence") if decision else None,
                "research_summary": research.get("summary") if research else None,
                "candidates": research.get("candidates") or [],
                "n_tools": len(tools_used),
                "cost_usd": decision.get("cost_usd") if decision else None,
                "nav_eod": round(nav_eod, 2) if nav_eod is not None else None,
                "day_change_pct": round(day_change_pct, 2) if day_change_pct is not None else None,
                "cumulative_ret_pct": round(cumulative_ret_pct, 2) if cumulative_ret_pct is not None else None,
                "position_ret_pct": round(position_ret_pct, 2) if position_ret_pct is not None else None,
                "qqq_ret_pct": round(qqq_ret_pct, 2) if qqq_ret_pct is not None else None,
            }
            days.append(day)

        # Most recent days first — easier to scan the latest narrative.
        days.reverse()
        journal.append({
            "name": p.name,
            "label": p.label,
            "accent": p.accent,
            "days": days,
        })
    return journal


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
    # Compare against TRUE total return (cash + positions vs $100k), the
    # same scale QQQ is measured on (its $100k fully deployed). Position-
    # level return is incomparable since QQQ has no cash.
    leaders_beating = sum(1 for r in leaderboard if r["total_ret_pct"] > ret_pct)
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
            # Total return: full account equity (cash + positions) vs $100k.
            # This is what matches the QQQ benchmark line (which is also a
            # total-return-on-$100k number).
            total_eq = float(r.get("nav", STARTING_CASH))
            total_ret = (total_eq / STARTING_CASH - 1) * 100
            points.append({
                "date": r["date"],
                "ret_pct": round(total_ret, 2),
                "portfolio_value": round(total_eq, 2),
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

    journal = _journal(decisions, nav_history)

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    html = tpl.render(
        day=_day_label(date.today()),
        leaderboard=leaderboard,
        benchmark=benchmark,
        journal=journal,
        today_decisions=[today_decisions.get(p.name) for p in PROVIDERS],
        providers=[{"name": p.name, "label": p.label, "accent": p.accent} for p in PROVIDERS],
        chart_series=chart_series,
        decisions=list(reversed(_enrich_decisions_with_fills(decisions, nav_history)))[:30],
        tool_usage=[{"ai": p.name, "label": p.label, "accent": p.accent, "counts": tool_usage.get(p.name, {})} for p in PROVIDERS],
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        starting_cash=STARTING_CASH,
        budget_per_ai=BUDGET_PER_AI,
        universe=UNIVERSE,
    )
    OUT_HTML.write_text(html)
    OUT_DATA.write_text(json.dumps({
        "leaderboard": leaderboard,
        "today_decisions": today_decisions,
        "chart_series": chart_series,
        "tool_usage": tool_usage,
        "total_cost_per_ai": cost_by_ai,
    }, default=str, indent=2))
