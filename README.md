# 🥊 LLM Trading Throwdown

**ChatGPT vs Claude vs Gemini · 30 trading days · $100k each · one move per day**

Every US weekday, three AIs each pick ONE move:
- `BUY <ticker> $X` (open or add to a position, min $5k, max 25% of NAV)
- `SELL <ticker>` (close the full position)
- `HOLD` (no action today)

Each AI's portfolio is a separate Alpaca paper account. End-of-day NAV is snapshotted. After 30 trading days, **highest NAV wins**.

Live dashboard: **[`sohail1708.github.io/llm-throwdown`](https://sohail1708.github.io/llm-throwdown/)**

## How it works

```
08:30 ET — daily.py (GitHub Actions)
            ↓ fetch market snapshot via yfinance (prices, RSI, headlines)
            ↓ fan out to 3 LLMs in parallel
            ↓ each picks one action; gets validated against the rules
            ↓ submit market-on-open order to that AI's Alpaca account
            ↓ append all 3 decisions to data/decisions.jsonl

16:30 ET — eod.py (GitHub Actions)
            ↓ read each Alpaca paper account's NAV + open positions
            ↓ append to data/nav.jsonl
            ↓ regenerate docs/index.html
            ↓ git commit + push → GitHub Pages auto-deploys
```

## Players

| AI | Model | Account env var prefix |
|----|-------|------------------------|
| ChatGPT | `gpt-4o-mini` (default) | `ALPACA_CHATGPT_*` |
| Claude  | `claude-haiku-4-5-20251001` | `ALPACA_CLAUDE_*` |
| Gemini  | `gemini-2.5-flash` (default) | `ALPACA_GEMINI_*` |

Override model with `OPENAI_MODEL` or `GEMINI_MODEL` env vars.

## Universe

AAPL · MSFT · NVDA · GOOGL · AMZN · META · TSLA · AVGO · ORCL. Long-only.

## Constraints (enforced by validator)

- One action per AI per day
- BUY: min $5,000, max 25% of NAV per position
- SELL: closes full position only (no partial sells)
- HOLD: free choice, no penalty
- Positions held overnight until SELL

## Run window

**Mon Jun 1, 2026 → Tue Jul 14, 2026** (30 trading days)

## Cost

- LLM: ~3 calls/day × 22 trading days × cheap models = **~$0.50–1.00 total for the month**
- Infra: $0 (GitHub Actions free tier + GitHub Pages)

## Local dev

```bash
pip install -e .
cp .env.example .env  # fill in 3 LLM keys + 3 Alpaca keypairs
python daily.py --dry-run   # see what each AI would do, no orders
python eod.py --rebuild-only   # regen dashboard from existing data
```

## Required GitHub Actions secrets

LLMs: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`

Alpaca (paper): `ALPACA_{CHATGPT,CLAUDE,GEMINI}_{KEY,SECRET}`
