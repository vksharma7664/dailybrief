# Daily Regulatory & Banking Brief

## Purpose
Daily 8:30 AM IST summary of RBI circulars, UPI/NPCI updates, IRDAI/SEBI
notifications, and banking news. Delivered to leadership via email + WhatsApp.

## Hard rules
1. Never include customer PII or internal company data in any prompt sent to Claude API.
2. Only fetch from sources listed in `config/sources.yaml`. No ad-hoc URLs.
3. Use official RSS where available (RBI publishes 4 RSS feeds — use them, do not scrape).
4. Respect robots.txt and rate-limit to ≤1 req/sec per domain.
5. All times in IST. Cron runs in UTC; convert at boundaries only.
6. Fail closed: if a source fails, log + email a health alert, do not silently drop.

## Stack
- Python 3.12, uv for env mgmt
- httpx (async), feedparser, selectolax, pypdf
- anthropic SDK (model: claude-sonnet-4-7-20250219, batch summaries)
- SQLite for dedupe state
- Twilio (phase 1) → Meta WhatsApp Cloud API (phase 2)
- SMTP via company relay or AWS SES

## Architecture
See README.md → Architecture section.

## What "done" looks like
- Brief delivered daily by 8:35 AM IST
- Top 3 items have a "why it matters" line
- WhatsApp message ≤ 4000 chars with link to full HTML brief
- Email contains full markdown brief inline
- Dedup correctly handles re-published RBI circulars (hash on title + first 500 chars of body)
- All Claude API calls batched (5 items per call max)

## Compliance posture
This agent reads only public regulatory and news sources, summarizes them via
Claude API, and sends to internal recipients. No customer data is processed.
No scraping of authenticated content. Document trail kept in data/archive/.