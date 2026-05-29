"""ChatGPT (OpenAI gpt-5) player — 2-phase pipeline with sentinel-tool output.

GPT-5 is a reasoning model and tends to write prose instead of strict JSON
even when instructed. We use sentinel tools (`submit_research`,
`submit_decision`) to force structured output — the same pattern we use
for Claude. This makes the output-extraction mechanism identical across
all 3 providers (max fairness)."""
from __future__ import annotations

import json
import os

from openai import OpenAI

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

NAME = "chatgpt"
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5")
MAX_TOOL_ITERATIONS = 5
# GPT-5 is a reasoning model — internal reasoning consumes max_completion_tokens
# BEFORE any visible output (including tool calls). Reasoning easily eats
# 1000-3000 tokens, so we raise caps high enough that the final tool call
# still has room to emit.
RESEARCH_MAX_TOKENS = 8000
DECISION_MAX_TOKENS = 4000

INPUT_PRICE_PER_M = float(os.environ.get("OPENAI_INPUT_PRICE", "1.25"))
OUTPUT_PRICE_PER_M = float(os.environ.get("OPENAI_OUTPUT_PRICE", "10.0"))


def _to_openai(schema: dict) -> dict:
    """OpenAI nests tool spec under `function` with `parameters`."""
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": schema["input_schema"],
        },
    }


def _research_tools() -> list[dict]:
    return [_to_openai(t) for t in TOOL_SCHEMAS] + [_to_openai(SUBMIT_RESEARCH_TOOL)]


def _cost(in_tok: int, out_tok: int) -> float:
    return round(in_tok / 1_000_000 * INPUT_PRICE_PER_M + out_tok / 1_000_000 * OUTPUT_PRICE_PER_M, 4)


def _research(client: OpenAI, user_message: str) -> tuple[Research, dict]:
    """Tool-using loop. Model commits by calling submit_research."""
    messages: list[dict] = [
        {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    tools_used: list[dict] = []
    in_tok = out_tok = 0

    for iteration in range(MAX_TOOL_ITERATIONS + 1):
        # On the last iteration, only expose submit_research and force it.
        if iteration == MAX_TOOL_ITERATIONS:
            tools = [_to_openai(SUBMIT_RESEARCH_TOOL)]
            tool_choice: str | dict = {"type": "function", "function": {"name": "submit_research"}}
        else:
            tools = _research_tools()
            tool_choice = "auto"

        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_completion_tokens=RESEARCH_MAX_TOKENS,
        )
        if resp.usage:
            in_tok += resp.usage.prompt_tokens
            out_tok += resp.usage.completion_tokens
        msg = resp.choices[0].message

        # Did the model call submit_research?
        if msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.function.name == "submit_research":
                    args = json.loads(tc.function.arguments or "{}")
                    research = parse_research_dict(args, tools_used=tools_used)
                    return research, {"input": in_tok, "output": out_tok}

            # Otherwise execute regular tools, continue loop.
            messages.append({
                "role": "assistant",
                "content": msg.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                tools_used.append({"name": tc.function.name, "args": args})
                result = call_tool(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
        else:
            # Model returned prose without committing — push it to commit.
            messages.append({"role": "assistant", "content": msg.content or ""})
            messages.append({
                "role": "user",
                "content": "Call the submit_research tool now with your final analysis.",
            })

    raise RuntimeError("chatgpt research: loop exhausted without submit_research")


def _decide(
    client: OpenAI,
    research_json: str,
    *,
    nav: float,
    cash: float,
    positions: list[dict],
) -> tuple[Decision, dict]:
    user = build_decision_context(research_json, nav=nav, cash=cash, positions=positions)
    resp = client.chat.completions.create(
        model=MODEL,
        max_completion_tokens=DECISION_MAX_TOKENS,
        messages=[
            {"role": "system", "content": DECISION_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        tools=[_to_openai(SUBMIT_DECISION_TOOL)],
        tool_choice={"type": "function", "function": {"name": "submit_decision"}},
    )
    msg = resp.choices[0].message
    if not msg.tool_calls:
        raise ValueError("chatgpt decision: model did not call submit_decision")
    args = json.loads(msg.tool_calls[0].function.arguments or "{}")
    decision = parse_decision_dict(
        args,
        nav=nav,
        positions_by_ticker={p["ticker"]: p["market_value"] for p in positions},
    )
    tok = {
        "input": resp.usage.prompt_tokens if resp.usage else 0,
        "output": resp.usage.completion_tokens if resp.usage else 0,
    }
    return decision, tok


def run(*, snaps: list[TickerSnapshot], nav: float, cash: float, positions: list[dict]) -> dict:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
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
