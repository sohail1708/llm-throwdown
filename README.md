# 🥊 LLM Trading Throwdown

**ChatGPT vs Claude vs Gemini · 30 trading days · $100k each · one move per day**

| | |
|---|---|
| 🌐 **Live dashboard** | **https://sohail1708.github.io/llm-throwdown/** |
| 💻 **Source code** | **https://github.com/sohail1708/llm-throwdown** |
| 📅 **Run window** | Mon Jun 1, 2026 → Tue Jul 14, 2026 (30 trading days) |

---

Every US weekday, three AIs each pick ONE move:
- `BUY <ticker> $X` — open a new position or add to an existing one (min $5k, max 25% of NAV)
- `SELL <ticker>` — close the full position (no partial sells)
- `HOLD` — no action today

Each AI has its own Alpaca paper account. End-of-day NAV is snapshotted and benchmarked against QQQ. After 30 trading days, **highest position-level return wins**.

## Pipeline

```
13:30 UTC (09:30 ET) — daily.py via GitHub Actions
    ↓ fetch market snapshot via yfinance (prices, RSI, headlines)
    ↓ fan out to 3 LLMs sequentially
    ↓ each runs research phase (≤5 tool iterations) → decision phase
    ↓ validator enforces universe / $5k min / 25% NAV cap
    ↓ submit market order to that AI's Alpaca paper account
    ↓ append decision + research artifact to data/decisions.jsonl

20:30 UTC (16:30 ET) — eod.py via GitHub Actions
    ↓ snapshot each AI's NAV + open positions
    ↓ pull official close prices from yfinance (NMS consolidated tape)
    ↓ snapshot QQQ benchmark
    ↓ append to data/nav.jsonl, regenerate docs/index.html
    ↓ commit + push → GitHub Pages republishes
```

Both crons schedule 3 redundant fires (e.g. 20:05 / 20:30 / 21:00 UTC) to hedge against GitHub Actions cron jitter; the underlying logic is idempotent.

## Players

| AI | Model |
|----|-------|
| ChatGPT | `gpt-5` |
| Claude | `claude-sonnet-4-6` |
| Gemini | `gemini-2.5-pro` |

Override with `OPENAI_MODEL` / `CLAUDE_MODEL` / `GEMINI_MODEL` env vars.

## Universe (long-only)

`AAPL` · `MSFT` · `NVDA` · `GOOGL` · `AMZN` · `META` · `TSLA` · `AVGO` · `ORCL`

The 9 largest NASDAQ tech names — maps cleanly to the QQQ benchmark.

## Shared tools (research phase)

Each AI gets a budget of 5 tool calls per day to investigate:

- `get_history(ticker, days)` — OHLCV bars
- `get_news(ticker, n)` — recent headlines
- `get_financials(ticker)` — P/E, growth, margins, debt
- `get_analyst_ratings(ticker)` — Street targets + buy/sell distribution
- `read_own_trades(ai, n)` — self-reflection on its own past trades

## Cost

| | |
|---|---|
| LLM (3 AIs × ~$0.13/day × ~22 trading days) | **~$3–5** |
| GitHub Actions cron | $0 (free for public repos) |
| GitHub Pages | $0 |
| Alpaca paper trading | $0 |
| yfinance market data | $0 |
| **Total for 30 days** | **~$4** |

## Security

- All secrets live in **`.env` (gitignored)** and **GitHub Actions repository secrets** — never committed.
- `.env.example` shows the required variable names with empty values.
- Repo is public; data files, code, and dashboard are all auditable. No keys, no PII.

## Local dev

```bash
pip install -e .
cp .env.example .env  # fill in 3 LLM keys + 3 Alpaca paper keypairs
python daily.py --dry-run     # run the pipeline, skip Alpaca order submission
python eod.py --rebuild-only  # regen dashboard from existing data.jsonl files
```

## Required GitHub Actions secrets

LLMs: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`

Alpaca paper: `ALPACA_CHATGPT_KEY`, `ALPACA_CHATGPT_SECRET`, `ALPACA_CLAUDE_KEY`, `ALPACA_CLAUDE_SECRET`, `ALPACA_GEMINI_KEY`, `ALPACA_GEMINI_SECRET`

## Files

| Path | What |
|---|---|
| `daily.py` | Morning orchestrator |
| `eod.py` | Evening NAV snapshot + dashboard rebuild |
| `src/llm_chatgpt.py` `llm_claude.py` `llm_gemini.py` | Provider adapters |
| `src/tools.py` | Shared tool registry + sentinel tools |
| `src/prompt.py` | Research + decision system prompts |
| `src/decision.py` | Validators (universe, sizing, position rules) |
| `src/broker.py` | Alpaca paper wrapper |
| `src/dashboard.py` `templates/index.html` | Jinja2 dashboard render |
| `data/decisions.jsonl` | Every decision + research artifact + cost |
| `data/nav.jsonl` | EOD NAV snapshots per AI per day, plus QQQ |
| `docs/index.html` `docs/data.json` | Published GitHub Pages content |
