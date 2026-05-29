"""Registry of the 3 contestants and how to invoke each."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from src import llm_chatgpt, llm_claude, llm_gemini


@dataclass(frozen=True)
class Provider:
    name: str              # slug used in JSONL filenames + tool's read_own_trades
    label: str             # human-facing display label
    accent: str            # tailwind accent name for dashboard cards
    alpaca_key_env: str
    alpaca_secret_env: str
    run: Callable[..., dict]   # returns {research, decision, tokens, cost_usd}


PROVIDERS: list[Provider] = [
    Provider(
        name="chatgpt",
        label="ChatGPT",
        accent="emerald",
        alpaca_key_env="ALPACA_CHATGPT_KEY",
        alpaca_secret_env="ALPACA_CHATGPT_SECRET",
        run=llm_chatgpt.run,
    ),
    Provider(
        name="claude",
        label="Claude",
        accent="orange",
        alpaca_key_env="ALPACA_CLAUDE_KEY",
        alpaca_secret_env="ALPACA_CLAUDE_SECRET",
        run=llm_claude.run,
    ),
    Provider(
        name="gemini",
        label="Gemini",
        accent="blue",
        alpaca_key_env="ALPACA_GEMINI_KEY",
        alpaca_secret_env="ALPACA_GEMINI_SECRET",
        run=llm_gemini.run,
    ),
]


def by_name(name: str) -> Provider:
    for p in PROVIDERS:
        if p.name == name:
            return p
    raise KeyError(name)
