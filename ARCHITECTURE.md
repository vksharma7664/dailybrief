# DailyBrief — Architecture & Developer Guide

> This document explains how every part of the agent works and how to set it
> up from scratch on a new server. Read this before making any code changes.

---

## 1. What this agent does

Every day at **8:30 AM IST** it:
1. Fetches the latest circulars / news from 9 sources (RBI, SEBI, IRDAI, NPCI, Mint, ET BFSI)
2. Deduplicates against yesterday's run
3. Scores articles by regulatory keyword relevance
4. Picks the top 3 most important items
5. Asks Claude to write a one-sentence "Why it matters" for each top-3 item
6. Renders a styled HTML email + a short WhatsApp message
7. Delivers to leadership via Gmail SMTP (+ Twilio WhatsApp when configured)

**Cost per run: ~₹0.002 (less than 1 paisa)** — only 3 Claude calls for "why it matters".

---

## 2. Full pipeline — step by step

```
                        ┌─────────────────────────────┐
                        │         main.py              │
                        │  (runs every step in order)  │
                        └──────────────┬──────────────┘
                                       │
               ┌───────────────────────▼──────────────────────┐
               │                  STEP 1: FETCH                │
               │                                               │
               │  config/sources.yaml ──► fetchers/            │
               │                                               │
               │  type: rss  ──► rss.py (feedparser)           │
               │  type: html ──► html_sebi.py  (selectolax)    │
               │               ► html_irdai.py                 │
               │               ► html_npci.py                  │
               │                                               │
               │  RBI notifications ──► pdf.py                 │
               │    (fetch_full_text: true → download PDF)      │
               │                                               │
               │  Output: list[Item]  (130 articles typical)   │
               └───────────────────────┬───────────────────────┘
                                       │
               ┌───────────────────────▼──────────────────────┐
               │               STEP 2: DEDUPE                  │
               │                                               │
               │  pipeline/dedupe.py + data/seen.db (SQLite)   │
               │                                               │
               │  hash = sha256(title + body[:500])            │
               │  If hash already in DB → drop                 │
               │  If new → keep + write hash to DB             │
               │                                               │
               │  Output: only articles not seen before        │
               └───────────────────────┬───────────────────────┘
                                       │
               ┌───────────────────────▼──────────────────────┐
               │               STEP 3: FILTER & SCORE          │
               │                                               │
               │  pipeline/filter.py                           │
               │                                               │
               │  Drop: older than 24 hours                    │
               │  Drop: matches exclude keyword                │
               │  Drop: near-duplicate title (Jaccard ≥ 80%)   │
               │                                               │
               │  Score = 1                                     │
               │        + 10 × (high priority keyword hits)    │
               │        + 5  × (medium priority keyword hits)  │
               │                                               │
               │  Keywords from: config/keywords.yaml          │
               │  Output: scored + sorted list[Item]           │
               └───────────────────────┬───────────────────────┘
                                       │
               ┌───────────────────────▼──────────────────────┐
               │           STEP 4: SUMMARIZE (optional)        │
               │                                               │
               │  pipeline/summarizer.py                       │
               │  Prompt: prompts/summarize_news.txt           │
               │                                               │
               │  SKIP_CLAUDE_SUMMARIZATION=true  (default)    │
               │    → shows raw RSS body, zero API calls       │
               │                                               │
               │  SKIP_CLAUDE_SUMMARIZATION=false              │
               │    → Claude rewrites each item body           │
               │    → batches 5 items per API call             │
               │    → sets item.summary                        │
               └───────────────────────┬───────────────────────┘
                                       │
               ┌───────────────────────▼──────────────────────┐
               │               STEP 5: RANK (top 3)            │
               │                                               │
               │  pipeline/ranker.py                           │
               │                                               │
               │  combined = (keyword_score × 0.7)             │
               │           + (claude_relevance × 0.3)          │
               │                                               │
               │  top3     → "Need to Know" section            │
               │  remaining → "Other Developments" section     │
               └───────────────────────┬───────────────────────┘
                                       │
               ┌───────────────────────▼──────────────────────┐
               │         STEP 6: WHY IT MATTERS  ← Claude     │
               │                                               │
               │  pipeline/why_it_matters.py                   │
               │  Prompt: prompts/why_it_matters.txt           │
               │                                               │
               │  SKIP_WHY_IT_MATTERS=false  (default)         │
               │    → 1 Claude call per top-3 item (3 calls)   │
               │    → sets item.why_it_matters                 │
               │    → Cost: ~₹0.002 per run                    │
               │                                               │
               │  SKIP_WHY_IT_MATTERS=true                     │
               │    → shows "[dry-run placeholder]"            │
               └───────────────────────┬───────────────────────┘
                                       │
               ┌───────────────────────▼──────────────────────┐
               │               STEP 7: RENDER                  │
               │                                               │
               │  render/email_html.py                         │
               │    + templates/email.html.j2 (Jinja2)         │
               │    → Full styled HTML email                   │
               │                                               │
               │  render/markdown.py                           │
               │    → Plain-text fallback                      │
               │                                               │
               │  render/whatsapp.py                           │
               │    → Short message ≤ 4000 chars               │
               └───────────────────────┬───────────────────────┘
                                       │
               ┌───────────────────────▼──────────────────────┐
               │               STEP 8: DELIVER                 │
               │                                               │
               │  delivery/email.py                            │
               │    → Always saves to data/archive/ first      │
               │    → DRY_RUN=false → sends via Gmail SMTP     │
               │    → DRY_RUN=true  → saved only, no send      │
               │    → On source failure → send_health_alert()  │
               │                                               │
               │  delivery/whatsapp.py                         │
               │    → Twilio API (skips if creds placeholder)  │
               └───────────────────────────────────────────────┘
```

---

## 3. Data model

```python
Item                        # One news article through the whole pipeline
  ├── id                    # sha256 hash (used for dedup + lookup)
  ├── title                 # Article headline
  ├── url                   # Source URL
  ├── body                  # Raw text (RSS description or PDF extract)
  ├── summary               # Claude-rewritten body (if SKIP_SUMMARIZATION=false)
  ├── why_it_matters        # Claude insight line (top-3 only)
  ├── category              # RBI | UPI | Banking | Markets | Insurance
  ├── source_id             # rbi_notifications | mint_banking | etc.
  ├── published_at          # datetime (always stored as UTC, displayed as IST)
  ├── score                 # keyword score from filter.py
  ├── relevance_score       # 1–10 from Claude (when summarization on)
  └── matched_keywords      # which keywords fired the score

Brief                       # The daily package passed to render + deliver
  ├── generated_at          # UTC timestamp of this run
  ├── date_range_start      # window start (IST)
  ├── date_range_end        # window end (IST)
  ├── top3                  # list[Item] — top 3 by combined score
  └── items                 # list[Item] — all items (top3 + remaining)
```

---

## 4. File map — what to edit for each change

| I want to… | Edit this |
|------------|-----------|
| Add a new news source | `config/sources.yaml` |
| Add/remove scoring keywords | `config/keywords.yaml` |
| Add/remove email recipients | `config/recipients.yaml` |
| Change email HTML design | `templates/email.html.j2` |
| Change body preview length | `templates/email.html.j2` line 51 (top-3) and line 105 (remaining) |
| Change Claude summary prompt | `prompts/summarize_news.txt` |
| Change "why it matters" prompt | `prompts/why_it_matters.txt` |
| Change ranking weights | `src/dailybrief/pipeline/ranker.py` |
| Change the 24h time window | `src/dailybrief/pipeline/filter.py` → `last_24h_window()` |
| Add a new HTML scraper | Create `src/dailybrief/fetchers/html_newsite.py`, register in `main.py` |
| Change secrets / toggles | `.env` |

---

## 5. `.env` controls explained

```bash
# ── Anthropic (Claude) ──────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...      # Required for why_it_matters + summarization

# ── Email (Gmail SMTP) ──────────────────────────────────────
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=no-reply@yourco.com     # Sender address
SMTP_PASS=xxxx xxxx xxxx xxxx     # Gmail App Password (not login password)
SMTP_FROM=no-reply@yourco.com     # Shows as "From" in inbox

# ── WhatsApp (Twilio) ───────────────────────────────────────
TWILIO_ACCOUNT_SID=ACxxxx...      # From twilio.com/console
TWILIO_AUTH_TOKEN=xxxx...
TWILIO_WHATSAPP_FROM=+14155238886 # Twilio sandbox or registered number

# ── Behaviour toggles ───────────────────────────────────────
DRY_RUN=false                     # true = local save only | false = real send
SKIP_CLAUDE_SUMMARIZATION=true    # true = raw RSS body (free, recommended)
                                  # false = Claude rewrites each body
SKIP_WHY_IT_MATTERS=false         # false = Claude writes insight (recommended)
                                  # true = shows placeholder

# ── Cost guardrails ─────────────────────────────────────────
MAX_RUN_COST_INR=50               # Hard stop if spend exceeds ₹50
MAX_CLAUDE_CALLS_PER_RUN=15       # Hard stop on number of API calls
```

---

## 6. Setting up on a new server (Ubuntu / Debian)

### 6.1 Install system dependencies

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv git curl
# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # or restart terminal
```

### 6.2 Clone the repo

```bash
git clone https://github.com/vksharma7664/dailybrief.git
cd dailybrief
```

### 6.3 Install Python dependencies

```bash
uv sync
```

### 6.4 Create and fill `.env`

```bash
cp .env.example .env
nano .env   # or vim .env
```

Fill in:
- `ANTHROPIC_API_KEY` — your Anthropic key
- `SMTP_USER` / `SMTP_PASS` — Gmail sender + App Password
- `DRY_RUN=false`
- `SKIP_WHY_IT_MATTERS=false`

### 6.5 Add recipients

```bash
nano config/recipients.yaml
```

```yaml
email:
  - name: "Boss"
    address: "boss@yourco.com"
whatsapp:
  - name: "Boss"
    number: "+91XXXXXXXXXX"
    enabled: true
```

### 6.6 Test run (no email sent)

```bash
uv run python -m dailybrief.main --dry-run --skip-dedupe --limit 5
```

Check `data/archive/` for the generated HTML file. Open it in a browser to preview.

### 6.7 Verify real send works

```bash
uv run python -m dailybrief.main --no-dry-run --limit 5
```

Check your inbox. Confirm "Why it matters" lines appear (not placeholder).

### 6.8 Schedule with cron (8:30 AM IST = 3:00 AM UTC)

```bash
crontab -e
```

Add this line:
```bash
0 3 * * * cd /home/ubuntu/dailybrief && /home/ubuntu/dailybrief/.venv/bin/python -m dailybrief.main --no-dry-run >> /home/ubuntu/dailybrief/logs/cron.log 2>&1
```

Verify cron is running:
```bash
grep CRON /var/log/syslog | tail -20
```

### 6.9 (Optional) Set up as a systemd service

```bash
sudo nano /etc/systemd/system/dailybrief.service
```

```ini
[Unit]
Description=Daily Regulatory Brief

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/dailybrief
ExecStart=/home/ubuntu/dailybrief/.venv/bin/python -m dailybrief.main --no-dry-run
StandardOutput=append:/home/ubuntu/dailybrief/logs/dailybrief.log
StandardError=append:/home/ubuntu/dailybrief/logs/dailybrief.log
```

```bash
sudo nano /etc/systemd/system/dailybrief.timer
```

```ini
[Unit]
Description=Run DailyBrief at 8:30 AM IST daily

[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dailybrief.timer
sudo systemctl list-timers | grep dailybrief   # verify
```

---

## 7. Setting up on Windows (Task Scheduler)

1. Open **Task Scheduler** → **Create Basic Task**
2. **Trigger**: Daily at **3:00 AM** (UTC) — this is 8:30 AM IST
3. **Action**: Start a program
   - Program: `C:\path\to\dailybrief\.venv\Scripts\python.exe`
   - Arguments: `-m dailybrief.main --no-dry-run`
   - Start in: `C:\path\to\dailybrief`
4. **General tab**: check "Run whether user is logged on or not"

---

## 8. Sources currently configured

| ID | Type | Category | Full text? |
|----|------|----------|------------|
| `rbi_notifications` | RSS | RBI | ✅ PDF fetched |
| `rbi_press` | RSS | RBI | ❌ |
| `rbi_publications` | RSS | RBI | ❌ |
| `rbi_speeches` | RSS | RBI | ❌ |
| `et_bfsi` | RSS | Banking | ❌ |
| `mint_banking` | RSS | Banking | ❌ |
| `npci_press` | HTML | UPI | ❌ (JS site — 0 items currently) |
| `sebi_circulars` | HTML | Markets | ❌ |
| `irdai_circulars` | HTML | Insurance | ❌ |

---

## 9. Known limitations / pending work

| Item | Detail |
|------|--------|
| **NPCI** returns 0 items | Their site requires JavaScript rendering. Needs Playwright scraper. |
| **WhatsApp** not active | Twilio credentials not configured. Phase 2 work. |
| **Archive URL** not live | `ARCHIVE_BASE_URL` points to placeholder domain. Needs hosting. |
| **No tests** | `tests/` directory exists but is empty. Unit tests pending. |
| **NPCI scraper** | Needs upgrade to headless browser (Playwright) |

---

## 10. Logs and monitoring

```
logs/
└── dailybrief.log     # rotating, 5 MB × 14 files (~2 weeks)

data/
├── seen.db            # production dedup state
├── seen_dryrun.db     # test run dedup state
└── archive/
    └── email_20260509T092710Z.html   # every brief saved here
```

**Healthy run looks like:**
```
INFO   Fetching 6 RSS + 3 HTML sources…
INFO   Total fetched: 130
INFO   After dedupe: 14 new items
INFO   After filter: 11 items in window
INFO   Top 3: ['RBI imposes penalty...', 'SBI credit...', ...]
INFO   Email sent to ['boss@yourco.com']
INFO   Run complete. Items: 11 | Claude calls: 3 | Total cost: ₹0.0021
```

**If a source fails:** a health-alert email is sent automatically to all recipients.
