"""Shared Decision + Research types and validators."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.data import UNIVERSE

MIN_BUY_NOTIONAL = 5_000.0
MAX_POSITION_PCT_NAV = 0.25
VALID_ACTIONS = {"BUY", "SELL", "HOLD"}
VALID_LEANS = {"BUY", "SELL", "HOLD"}


@dataclass
class Candidate:
    ticker: str
    bull: str
    bear: str
    lean: str  # BUY | SELL | HOLD


@dataclass
class Research:
    candidates: list[Candidate]
    summary: str
    preferred_action: str           # BUY | SELL | HOLD
    preferred_ticker: str | None
    tools_used: list[dict] = field(default_factory=list)  # [{"name", "args"}]
    raw_json: str = ""


@dataclass
class Decision:
    action: str
    ticker: str | None
    amount: float | None
    reason: str
    confidence: int


def _strip_codefence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    return t


def parse_research_dict(data: dict, *, tools_used: list[dict] | None = None) -> Research:
    """Build a Research from an already-parsed dict (e.g. submit_research args)."""
    cands_raw = data.get("candidates") or []
    candidates: list[Candidate] = []
    for c in cands_raw[:5]:
        ticker = str(c.get("ticker", "")).upper()
        if ticker not in UNIVERSE:
            continue
        lean = str(c.get("lean", "HOLD")).upper()
        if lean not in VALID_LEANS:
            lean = "HOLD"
        candidates.append(Candidate(
            ticker=ticker,
            bull=str(c.get("bull", "")).strip()[:300],
            bear=str(c.get("bear", "")).strip()[:300],
            lean=lean,
        ))
    summary = str(data.get("summary", "")).strip()[:400]
    preferred_action = str(data.get("preferred_action", "HOLD")).upper()
    if preferred_action not in VALID_ACTIONS:
        preferred_action = "HOLD"
    preferred_ticker = data.get("preferred_ticker") or None
    if preferred_ticker:
        preferred_ticker = str(preferred_ticker).upper()
        if preferred_ticker not in UNIVERSE:
            preferred_ticker = None
    return Research(
        candidates=candidates,
        summary=summary,
        preferred_action=preferred_action,
        preferred_ticker=preferred_ticker,
        tools_used=tools_used or [],
        raw_json=json.dumps(data),
    )


def parse_decision_dict(data: dict, *, nav: float, positions_by_ticker: dict[str, float]) -> Decision:
    """Build a Decision from an already-parsed dict (e.g. submit_decision args)."""
    action = str(data.get("action", "")).upper()
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid action: {action!r}")
    reason = str(data.get("reason", "")).strip() or "(no reason given)"
    try:
        confidence = max(1, min(5, int(data.get("confidence", 3))))
    except Exception:
        confidence = 3
    if action == "HOLD":
        return Decision(action="HOLD", ticker=None, amount=None, reason=reason, confidence=confidence)

    ticker = str(data.get("ticker", "")).upper()
    if ticker not in UNIVERSE:
        raise ValueError(f"ticker not in universe: {ticker!r}")

    if action == "SELL":
        if ticker not in positions_by_ticker or positions_by_ticker[ticker] <= 0:
            raise ValueError(f"SELL but no position in {ticker}")
        return Decision(action="SELL", ticker=ticker, amount=None, reason=reason, confidence=confidence)

    # BUY
    try:
        amount = float(data.get("amount", 0))
    except Exception:
        amount = 0.0
    if amount < MIN_BUY_NOTIONAL:
        raise ValueError(
            f"BUY amount ${amount:.0f} below minimum ${MIN_BUY_NOTIONAL:.0f}"
        )
    cap = nav * MAX_POSITION_PCT_NAV
    current_value = positions_by_ticker.get(ticker, 0.0)
    headroom = max(0.0, cap - current_value)
    if amount > headroom:
        amount = round(headroom, 2)
        if amount < MIN_BUY_NOTIONAL:
            raise ValueError(
                f"{ticker} hits 25% NAV cap; only ${headroom:.0f} headroom (below min trade)"
            )
    return Decision(
        action="BUY", ticker=ticker, amount=round(amount, 2),
        reason=reason, confidence=confidence,
    )


def parse_research(raw: str, *, tools_used: list[dict] | None = None) -> Research:
    text = _strip_codefence(raw)
    data = json.loads(text)

    cands_raw = data.get("candidates") or []
    candidates: list[Candidate] = []
    for c in cands_raw[:5]:
        ticker = str(c.get("ticker", "")).upper()
        if ticker not in UNIVERSE:
            continue
        lean = str(c.get("lean", "HOLD")).upper()
        if lean not in VALID_LEANS:
            lean = "HOLD"
        candidates.append(Candidate(
            ticker=ticker,
            bull=str(c.get("bull", "")).strip()[:300],
            bear=str(c.get("bear", "")).strip()[:300],
            lean=lean,
        ))

    summary = str(data.get("summary", "")).strip()[:400]
    preferred_action = str(data.get("preferred_action", "HOLD")).upper()
    if preferred_action not in VALID_ACTIONS:
        preferred_action = "HOLD"
    preferred_ticker = data.get("preferred_ticker")
    if preferred_ticker:
        preferred_ticker = str(preferred_ticker).upper()
        if preferred_ticker not in UNIVERSE:
            preferred_ticker = None

    return Research(
        candidates=candidates,
        summary=summary,
        preferred_action=preferred_action,
        preferred_ticker=preferred_ticker,
        tools_used=tools_used or [],
        raw_json=text,
    )


def parse_and_validate_decision(
    raw: str, *, nav: float, positions_by_ticker: dict[str, float]
) -> Decision:
    text = _strip_codefence(raw)
    data = json.loads(text)

    action = str(data.get("action", "")).upper()
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid action: {action!r}")
    reason = str(data.get("reason", "")).strip() or "(no reason given)"
    try:
        confidence = max(1, min(5, int(data.get("confidence", 3))))
    except Exception:
        confidence = 3

    if action == "HOLD":
        return Decision(action="HOLD", ticker=None, amount=None, reason=reason, confidence=confidence)

    ticker = str(data.get("ticker", "")).upper()
    if ticker not in UNIVERSE:
        raise ValueError(f"ticker not in universe: {ticker!r}")

    if action == "SELL":
        if ticker not in positions_by_ticker or positions_by_ticker[ticker] <= 0:
            raise ValueError(f"SELL but no position in {ticker}")
        return Decision(action="SELL", ticker=ticker, amount=None, reason=reason, confidence=confidence)

    # BUY path
    amount = float(data.get("amount", 0))
    if amount < MIN_BUY_NOTIONAL:
        raise ValueError(
            f"BUY amount ${amount:.0f} below minimum ${MIN_BUY_NOTIONAL:.0f}"
        )
    cap = nav * MAX_POSITION_PCT_NAV
    current_value = positions_by_ticker.get(ticker, 0.0)
    headroom = max(0.0, cap - current_value)
    if amount > headroom:
        amount = round(headroom, 2)
        if amount < MIN_BUY_NOTIONAL:
            raise ValueError(
                f"{ticker} hits 25% NAV cap; only ${headroom:.0f} headroom (below min trade)"
            )
    return Decision(
        action="BUY",
        ticker=ticker,
        amount=round(amount, 2),
        reason=reason,
        confidence=confidence,
    )
