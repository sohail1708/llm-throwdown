"""Claude Sonnet 4.6 player — 2-phase pipeline with sentinel-tool structured output.

Sonnet 4.6 doesn't support assistant message prefill, so we use sentinel
tools (`submit_research`, `submit_decision`) to extract structured output.
The model MUST call the sentinel tool to commit its answer; we read the
arguments as the result. Same mechanism the OpenAI/Gemini JSON modes give
us natively, just via tools.
"""
from __future__ import annotations

import os

import anthropic

from src.data import TickerSnapshot
from src.decision import (
    Decision,
    Research,
    parse_decision_dict,
    parse_research_dict,
)
from src.prompt import (
    DECISION_SYSTEM_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
    build_decision_context,
    build_research_context,
    today_iso,
)
from src.tools import (
    SUBMIT_DECISION_TOOL,
    SUBMIT_RESEARCH_TOOL,
    TOOL_SCHEMAS,
    call_tool,
)

NAME = "claude"
# Downgraded from sonnet-4-6 to haiku-4-5: 3x cheaper ($1/M in, $5/M out).
# Sonnet was burning ~90% of cost on input due to multi-turn tool loops
# re-sending prior tool results. Haiku is fast and capable enough for
# this 9-ticker decision space.
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
MAX_TOOL_ITERATIONS = 5
RESEARCH_MAX_TOKENS = 3000
DECISION_MAX_TOKENS = 800

INPUT_PRICE_PER_M = float(os.environ.get("CLAUDE_INPUT_PRICE", "1.0"))
OUTPUT_PRICE_PER_M = float(os.environ.get("CLAUDE_OUTPUT_PRICE", "5.0"))


def _to_anthropic(schema: dict) -> dict:
    return {
        "name": schema["name"],
        "description": schema["description"],
        "input_schema": schema["input_schema"],
    }


def _research_tools() -> list[dict]:
    return [_to_anthropic(t) for t in TOOL_SCHEMAS] + [_to_anthropic(SUBMIT_RESEARCH_TOOL)]


def _cost(in_tok: int, out_tok: int) -> float:
    return round(in_tok / 1_000_000 * INPUT_PRICE_PER_M + out_tok / 1_000_000 * OUTPUT_PRICE_PER_M, 4)


def _research(client: anthropic.Anthropic, user_message: str) -> tuple[Research, dict]:
    """Tool-using loop. Model commits by calling `submit_research`."""
    messages: list[dict] = [{"role": "user", "content": user_message}]
    tools_used: list[dict] = []
    in_tok = out_tok = 0

    for iteration in range(MAX_TOOL_ITERATIONS + 1):
        # On the last possible iteration force the model to commit by only
        # allowing the submit tool.
        tools = _research_tools()
        tool_choice: dict | None = None
        if iteration == MAX_TOOL_ITERATIONS:
            tools = [_to_anthropic(SUBMIT_RESEARCH_TOOL)]
            tool_choice = {"type": "tool", "name": "submit_research"}

        kwargs: dict = {
            "model": MODEL,
            "max_tokens": RESEARCH_MAX_TOKENS,
            "system": RESEARCH_SYSTEM_PROMPT,
            "tools": tools,
            "messages": messages,
        }
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        resp = client.messages.create(**kwargs)
        in_tok += resp.usage.input_tokens
        out_tok += resp.usage.output_tokens

        tool_calls = [b for b in resp.content if b.type == "tool_use"]

        # Did the model call submit_research?
        for tc in tool_calls:
            if tc.name == "submit_research":
                research = parse_research_dict(dict(tc.input), tools_used=tools_used)
                return research, {"input": in_tok, "output": out_tok}

        # Otherwise execute regular tools and continue.
        if not tool_calls:
            # Model returned only text without committing — prompt it to commit.
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({
                "role": "user",
                "content": "Call the submit_research tool now with your final analysis.",
            })
            continue

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for tc in tool_calls:
            tools_used.append({"name": tc.name, "args": dict(tc.input)})
            result = call_tool(tc.name, dict(tc.input))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": str(result),
            })
        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError("claude research: loop exhausted without submit_research")


def _decide(
    client: anthropic.Anthropic,
    research_json: str,
    *,
    nav: float,
    cash: float,
    positions: list[dict],
) -> tuple[Decision, dict]:
    user = build_decision_context(research_json, nav=nav, cash=cash, positions=positions)
    # Force the model to call submit_decision — no prose path.
    resp = client.messages.create(
        model=MODEL,
        max_tokens=DECISION_MAX_TOKENS,
        system=DECISION_SYSTEM_PROMPT,
        tools=[_to_anthropic(SUBMIT_DECISION_TOOL)],
        tool_choice={"type": "tool", "name": "submit_decision"},
        messages=[{"role": "user", "content": user}],
    )
    tool_calls = [b for b in resp.content if b.type == "tool_use" and b.name == "submit_decision"]
    if not tool_calls:
        raise ValueError(
            f"claude decision: model did not call submit_decision; "
            f"stop_reason={resp.stop_reason}"
        )
    decision = parse_decision_dict(
        dict(tool_calls[0].input),
        nav=nav,
        positions_by_ticker={p["ticker"]: p["market_value"] for p in positions},
    )
    return decision, {"input": resp.usage.input_tokens, "output": resp.usage.output_tokens}


def run(*, snaps: list[TickerSnapshot], nav: float, cash: float, positions: list[dict]) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    user_msg = build_research_context(
        snaps, ai_slug=NAME, today=today_iso(), nav=nav, cash=cash, positions=positions,
    )
    research, t_research = _research(client, user_msg)
    decision, t_decision = _decide(client, research.raw_json, nav=nav, cash=cash, positions=positions)
    total_in = t_research["input"] + t_decision["input"]
    total_out = t_research["output"] + t_decision["output"]
    return {
        "research": research,
        "decision": decision,
        "tokens": {"input": total_in, "output": total_out},
        "cost_usd": _cost(total_in, total_out),
    }
