# Daily Regulatory & Banking Brief

Daily 8:30 AM IST summary of RBI, UPI/NPCI, IRDAI, SEBI, and banking news.
Delivered via email and WhatsApp.

See `CLAUDE.md` for full context, hard rules, and compliance posture.

## Architecture
Sources (RSS + HTML) → Pipeline (fetch → dedupe → filter → summarize → rank)
→ Delivery (email + WhatsApp).

## Quick start
```bash
uv sync
uv run python -m dailybrief.main --dry-run
```