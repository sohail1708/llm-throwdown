"""Gemini 2.5 Pro player — 2-phase pipeline with function calling."""
from __future__ import annotations

import json
import os

from google import genai
from google.genai import types

from src.data import TickerSnapshot
from src.decision import Decision, Research, parse_and_validate_decision, parse_research
from src.prompt import (
    DECISION_SYSTEM_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
    build_decision_context,
    build_research_context,
    today_iso,
)
from src.tools import TOOL_SCHEMAS, call_tool

NAME = "gemini"
# Downgraded from 2.5-pro to 2.5-flash: 4x cheaper ($0.30/M in, $2.50/M out).
# 2.5-flash still does "thinking" but the budget is smaller; visible
# output usually lands in under 1000 tokens.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_TOOL_ITERATIONS = 5
RESEARCH_MAX_TOKENS = 4000
DECISION_MAX_TOKENS = 2000

INPUT_PRICE_PER_M = float(os.environ.get("GEMINI_INPUT_PRICE", "0.30"))
OUTPUT_PRICE_PER_M = float(os.environ.get("GEMINI_OUTPUT_PRICE", "2.50"))


def _to_gemini_tools() -> list[types.Tool]:
    """Gemini wants function declarations under a single Tool."""
    declarations = []
    for t in TOOL_SCHEMAS:
        declarations.append(types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=t["input_schema"],
        ))
    return [types.Tool(function_declarations=declarations)]


def _cost(in_tok: int, out_tok: int) -> float:
    return round(in_tok / 1_000_000 * INPUT_PRICE_PER_M + out_tok / 1_000_000 * OUTPUT_PRICE_PER_M, 4)


def _research(client: genai.Client, user_message: str) -> tuple[Research, dict]:
    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part.from_text(text=user_message)]),
    ]
    tools_used: list[dict] = []
    in_tok = out_tok = 0
    iterations = 0

    while iterations < MAX_TOOL_ITERATIONS:
        resp = client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=RESEARCH_SYSTEM_PROMPT,
                tools=_to_gemini_tools(),
                max_output_tokens=RESEARCH_MAX_TOKENS,
            ),
        )
        usage = getattr(resp, "usage_metadata", None)
        if usage:
            in_tok += getattr(usage, "prompt_token_count", 0) or 0
            out_tok += getattr(usage, "candidates_token_count", 0) or 0

        candidates = getattr(resp, "candidates", []) or []
        if not candidates:
            break
        cand = candidates[0]
        parts = cand.content.parts if cand.content else []
        fn_calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

        if not fn_calls:
            final_text = (resp.text or "").strip()
            research = parse_research(final_text, tools_used=tools_used)
            return research, {"input": in_tok, "output": out_tok}

        # Echo model's function-call turn into history, then add the
        # function-response turn.
        contents.append(cand.content)
        response_parts = []
        for fc in fn_calls:
            args = dict(fc.args or {})
            tools_used.append({"name": fc.name, "args": args})
            result = call_tool(fc.name, args)
            response_parts.append(types.Part.from_function_response(
                name=fc.name, response={"result": result}
            ))
        contents.append(types.Content(role="user", parts=response_parts))
        iterations += 1

    # Cap hit — force a final answer.
    # max_output_tokens kept at 2000 for parity with the OpenAI provider's
    # fallback path (which inherits research_max_tokens=2000).
    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_text(text="Tool budget exhausted. Output the final research JSON now.")],
    ))
    resp = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=RESEARCH_SYSTEM_PROMPT,
            response_mime_type="application/json",
            max_output_tokens=2000,
        ),
    )
    usage = getattr(resp, "usage_metadata", None)
    if usage:
        in_tok += getattr(usage, "prompt_token_count", 0) or 0
        out_tok += getattr(usage, "candidates_token_count", 0) or 0
    final_text = (resp.text or "").strip()
    research = parse_research(final_text, tools_used=tools_used)
    return research, {"input": in_tok, "output": out_tok}


def _decide(client: genai.Client, research_json: str, *, nav: float, cash: float, positions: list[dict]) -> tuple[Decision, dict]:
    user = build_decision_context(research_json, nav=nav, cash=cash, positions=positions)
    resp = client.models.generate_content(
        model=MODEL,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=DECISION_SYSTEM_PROMPT,
            response_mime_type="application/json",
            max_output_tokens=DECISION_MAX_TOKENS,
        ),
    )
    text = (resp.text or "").strip()
    if not text:
        finish_reasons = [getattr(c, "finish_reason", None) for c in (resp.candidates or [])]
        raise ValueError(
            f"gemini decision returned empty text; finish_reasons={finish_reasons}"
        )
    decision = parse_and_validate_decision(
        text, nav=nav, cash=cash,
        positions_by_ticker={p["ticker"]: p["market_value"] for p in positions},
    )
    usage = getattr(resp, "usage_metadata", None)
    tok = {
        "input":  getattr(usage, "prompt_token_count", 0) or 0 if usage else 0,
        "output": getattr(usage, "candidates_token_count", 0) or 0 if usage else 0,
    }
    return decision, tok


def run(*, snaps: list[TickerSnapshot], nav: float, cash: float, positions: list[dict]) -> dict:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
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
