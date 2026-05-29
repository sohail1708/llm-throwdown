"""Per-AI Alpaca paper-trading wrapper.

Each `Trader` instance is bound to one paper account (one set of API
credentials). All ops are scoped to that account.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest


@dataclass
class Account:
    cash: float
    equity: float
    portfolio_value: float


@dataclass
class Position:
    ticker: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pl: float


@dataclass
class OrderResult:
    id: str
    status: str


class Trader:
    """Thin wrapper around alpaca-py scoped to a single paper account."""

    def __init__(self, key_env: str, secret_env: str):
        api_key = os.environ[key_env]
        secret = os.environ[secret_env]
        self._client = TradingClient(api_key, secret, paper=True)

    def account(self) -> Account:
        a = self._client.get_account()
        return Account(
            cash=float(a.cash),
            equity=float(a.equity),
            portfolio_value=float(a.portfolio_value),
        )

    def positions(self) -> list[Position]:
        out: list[Position] = []
        for p in self._client.get_all_positions():
            out.append(Position(
                ticker=p.symbol,
                qty=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                market_value=float(p.market_value),
                unrealized_pl=float(p.unrealized_pl),
            ))
        return out

    def buy(self, ticker: str, notional: float) -> OrderResult:
        req = MarketOrderRequest(
            symbol=ticker,
            notional=round(notional, 2),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        o = self._client.submit_order(req)
        return OrderResult(id=str(o.id), status=str(o.status.value).lower())

    def close_position(self, ticker: str) -> OrderResult:
        o = self._client.close_position(ticker)
        return OrderResult(id=str(o.id), status=str(getattr(o, "status", "submitted")).lower())
