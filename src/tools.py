"""Shared tools the LLM agents can call during the research phase.

All tools return plain dicts so each provider SDK can serialize them to its
own tool-result format. Tool definitions in `TOOL_SCHEMAS` use a generic
JSON-Schema shape that maps cleanly to OpenAI, Anthropic, and Gemini.

Hard rule: tools never raise. Errors come back as `{"error": "..."}` so the
model can keep going instead of the whole pipeline crashing on a missing
data point.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import yfinance as yf

DECISIONS_PATH = Path(__file__).parent.parent / "data" / "decisions.jsonl"


def _safe(fn: Callable[..., dict]) -> Callable[..., dict]:
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"[:200]}
    return wrapper


@_safe
def get_history(ticker: str, days: int = 14) -> dict:
    """Default trimmed to 14 days (was 30) and volume removed — each row
    drops from ~60 tokens to ~30 tokens, cutting tool result size in half.
    14 days is enough for any short-term technical read (RSI, breakouts,
    pullbacks); deeper history is rarely used by the models in practice.
    """
    t = yf.Ticker(ticker)
    h = t.history(period=f"{max(days, 5)}d", auto_adjust=False)
    if h.empty:
        return {"ticker": ticker, "error": "no data"}
    out = []
    for d, row in h.tail(days).iterrows():
        out.append({
            "date": d.strftime("%Y-%m-%d"),
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
        })
    return {"ticker": ticker, "history": out}


@_safe
def get_news(ticker: str, n: int = 3) -> dict:
    """Default trimmed to 3 headlines (was 5). Publishers dropped — they're
    almost never load-bearing for the decision and add ~10 tokens per row."""
    t = yf.Ticker(ticker)
    news = (t.news or [])[:n]
    items = []
    for it in news:
        title = it.get("title") or it.get("content", {}).get("title")
        if title:
            items.append(title)
    return {"ticker": ticker, "headlines": items}


@_safe
def get_financials(ticker: str) -> dict:
    """Slimmed: dropped operating_margin (redundant w/ profit_margin) and
    dividend_yield (irrelevant for these growth names). Net ~25% smaller."""
    t = yf.Ticker(ticker)
    info = t.info or {}
    return {
        "ticker": ticker,
        "pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "market_cap": info.get("marketCap"),
        "rev_growth": info.get("revenueGrowth"),
        "profit_margin": info.get("profitMargins"),
        "debt_equity": info.get("debtToEquity"),
        "free_cashflow": info.get("freeCashflow"),
    }


@_safe
def get_analyst_ratings(ticker: str) -> dict:
    """Slimmed: removed last_period buy/sell distribution (the
    recommendation_mean already summarises it numerically). Saves ~80 tokens."""
    t = yf.Ticker(ticker)
    info = t.info or {}
    return {
        "ticker": ticker,
        "current_price": info.get("currentPrice"),
        "target_mean": info.get("targetMeanPrice"),
        "target_high": info.get("targetHighPrice"),
        "target_low": info.get("targetLowPrice"),
        "recommendation_mean": info.get("recommendationMean"),
        "number_of_analysts": info.get("numberOfAnalystOpinions"),
    }


@_safe
def read_own_trades(ai: str, n: int = 10) -> dict:
    if not DECISIONS_PATH.exists():
        return {"ai": ai, "trades": []}
    rows = []
    for line in DECISIONS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("ai") != ai or r.get("dry_run"):
            continue
        rows.append({
            "date": r["date"],
            "action": r["action"],
            "ticker": r.get("ticker"),
            "amount": r.get("amount"),
            "reason": r.get("reason"),
        })
    return {"ai": ai, "trades": rows[-n:]}


# Name → callable registry used by every provider's tool loop.
TOOL_REGISTRY: dict[str, Callable[..., dict]] = {
    "get_history": get_history,
    "get_news": get_news,
    "get_financials": get_financials,
    "get_analyst_ratings": get_analyst_ratings,
    "read_own_trades": read_own_trades,
}


# Generic tool schemas. Each provider's adapter translates these to its SDK's
# tool format (OpenAI: nested under "function", Anthropic: top-level,
# Gemini: function_declarations).
TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_history",
        "description": (
            "Daily OHLC bars for a ticker. Use to inspect price action, gaps, "
            "support/resistance. Default 14 days; max 60."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Ticker, e.g. AAPL"},
                "days":   {"type": "integer", "description": "Trading days back", "default": 14},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_news",
        "description": "Recent headlines for a ticker. Use to gauge catalysts and sentiment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "n":      {"type": "integer", "default": 3},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_financials",
        "description": (
            "Fundamentals: P/E, forward P/E, market cap, revenue growth, margins, "
            "debt-to-equity, FCF. Use to assess valuation vs growth."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_analyst_ratings",
        "description": (
            "Wall St analyst targets and buy/sell distribution. Use to gauge Street view."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "read_own_trades",
        "description": (
            "Read your OWN past trades and rationales. Pass your own slug as `ai` "
            "(chatgpt | claude | gemini). Use for self-reflection."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ai": {"type": "string"},
                "n":  {"type": "integer", "default": 10},
            },
            "required": ["ai"],
        },
    },
]


def call_tool(name: str, args: dict) -> dict:
    """Execute a named tool. Unknown tools return an error dict, never raise."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name}"}
    return fn(**args)


# Sentinel tools — the model MUST call one of these to commit its final
# output. We don't execute them; we extract their arguments as the structured
# answer. This is the provider-agnostic alternative to JSON-mode / prefill
# (which Sonnet 4.6 doesn't support).

SUBMIT_RESEARCH_TOOL = {
    "name": "submit_research",
    "description": (
        "Call this to commit your final research analysis. The arguments you "
        "pass ARE your structured output — do not write any prose afterward. "
        "This ends the research phase."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "description": "2-3 candidate setups you considered.",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "bull":   {"type": "string", "description": "One-sentence bull case."},
                        "bear":   {"type": "string", "description": "One-sentence bear case."},
                        "lean":   {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
                    },
                    "required": ["ticker", "bull", "bear", "lean"],
                },
            },
            "summary":          {"type": "string", "description": "One-sentence read of today's setup."},
            "preferred_action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
            "preferred_ticker": {"type": "string", "description": "Ticker for the preferred action, or empty for HOLD."},
        },
        "required": ["candidates", "summary", "preferred_action"],
    },
}


SUBMIT_DECISION_TOOL = {
    "name": "submit_decision",
    "description": (
        "Call this to commit today's single trade decision. The arguments you "
        "pass ARE your final action."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action":     {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
            "ticker":     {"type": "string", "description": "Ticker symbol; empty for HOLD."},
            "amount":     {"type": "number", "description": "Dollar notional for BUY; 0 for SELL/HOLD."},
            "reason":     {"type": "string", "description": "<=30 words, casual trader voice."},
            "confidence": {"type": "integer", "description": "1 (coin-flip) to 5 (high conviction)."},
        },
        "required": ["action", "reason", "confidence"],
    },
}
