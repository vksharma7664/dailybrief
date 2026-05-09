# DailyBrief — Operator Runbook

Daily 8:30 AM IST brief of RBI, SEBI, IRDAI, UPI/NPCI updates + banking news.
Delivered to leadership via email (+ WhatsApp once Twilio is configured).

---

## 1. First-time setup

### Prerequisites
- Python 3.12
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — `pip install uv`
- Git

### Clone & install
```bash
git clone https://github.com/vksharma7664/dailybrief.git
cd dailybrief
uv sync
```

### Configure secrets — copy and fill `.env`
```bash
cp .env.example .env
```

Open `.env` and set these values:

| Key | What to put |
|-----|-------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key (uncomment the line) |
| `SMTP_HOST` | `smtp.gmail.com` (already set) |
| `SMTP_USER` | Sender Gmail / Google Workspace address |
| `SMTP_PASS` | Gmail App Password (16-char, spaces OK) |
| `SMTP_FROM` | Same as `SMTP_USER` |
| `TWILIO_ACCOUNT_SID` | Twilio SID (leave `AC...` until Phase 2) |
| `TWILIO_AUTH_TOKEN` | Twilio token (leave `...` until Phase 2) |
| `TWILIO_WHATSAPP_FROM` | Twilio sandbox number (Phase 2) |

### Configure recipients — `config/recipients.yaml`
```yaml
email:
  - name: "Boss"
    address: "recipient@example.com"

whatsapp:
  - name: "Boss"
    number: "+91XXXXXXXXXX"
    enabled: true
```

---

## 2. Key switches in `.env`

| Setting | Default | Meaning |
|---------|---------|---------|
| `DRY_RUN` | `true` | `true` = save HTML locally only; `false` = send real email |
| `SKIP_CLAUDE_SUMMARIZATION` | `true` | `true` = show raw RSS body (free); `false` = Claude rewrites each item |
| `SKIP_WHY_IT_MATTERS` | `false` | `false` = Claude writes the "Why it matters" line for top-3 (recommended) |

**Recommended prod `.env` tail:**
```
DRY_RUN=false
SKIP_CLAUDE_SUMMARIZATION=true
SKIP_WHY_IT_MATTERS=false
```

---

## 3. Running the agent

### Test run (no email sent, no Claude cost)
```bash
uv run python -m dailybrief.main --dry-run --skip-dedupe --limit 5
```
- `--dry-run` — saves HTML to `data/archive/` instead of sending
- `--skip-dedupe` — bypasses the seen-items database (use for testing)
- `--limit 5` — process only 5 items (cost control)

### Production run (real email, Claude "why it matters")
```bash
uv run python -m dailybrief.main --no-dry-run
```

### Force-resend today's brief (ignore dedupe)
```bash
uv run python -m dailybrief.main --no-dry-run --skip-dedupe --limit 10
```

### Backfill a specific date range
```bash
uv run python -m dailybrief.main --no-dry-run --since "06 May 2026 00:00"
```

---

## 4. CLI flags reference

| Flag | Description |
|------|-------------|
| `--dry-run` | Save HTML locally, skip SMTP (overrides `DRY_RUN=false` in `.env`) |
| `--no-dry-run` | Send real email (overrides `DRY_RUN=true` in `.env`) |
| `--skip-dedupe` | Bypass SQLite deduplication — process all fetched items, mark nothing as seen |
| `--limit N` | Cap items processed at N (cost/time control during testing) |
| `--since "DD Mon YYYY HH:MM"` | Override the 24-hour window start for backfill |

---

## 5. Scheduling — daily 8:30 AM IST (3:00 AM UTC)

### Windows Task Scheduler
1. Open **Task Scheduler** → Create Basic Task
2. Trigger: Daily at **3:00 AM**
3. Action: Start a program
   - Program: `C:\path\to\dailybrief\.venv\Scripts\python.exe`
   - Arguments: `-m dailybrief.main --no-dry-run`
   - Start in: `C:\path\to\dailybrief`

### Linux / Mac cron
```bash
crontab -e
# Add:
0 3 * * * cd /path/to/dailybrief && /path/to/.venv/bin/python -m dailybrief.main --no-dry-run >> /path/to/dailybrief/logs/cron.log 2>&1
```

---

## 6. What a successful run looks like

```
15:24:19 INFO   Fetching 6 RSS + 3 HTML sources…
15:24:19 INFO   [rbi_notifications] 10 items fetched
15:24:19 INFO   Total fetched: 130
15:24:19 INFO   After filter: 16 items in window
15:24:19 INFO   After near-dup: 13 items (dropped 3)
15:24:19 INFO   Top 3: ['RBI imposes penalty...', 'SBI credit growth...', ...]
15:24:25 INFO   Email sent to ['boss@company.com']
15:24:25 INFO   Run complete. Items: 13 | Claude calls: 3 | Total cost: ₹0.0021
```

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `Could not resolve authentication method` in why_it_matters | `ANTHROPIC_API_KEY` is empty | Uncomment the key in `.env` |
| `[WhatsApp] Twilio credentials not configured` | Twilio creds are placeholders | Expected until Phase 2 — ignore |
| Email not received | SMTP misconfigured | Check `SMTP_USER`, `SMTP_PASS` (use Gmail App Password, not login password) |
| `[npci_press] 0 items fetched` | NPCI site requires JavaScript | Known limitation — NPCI needs a Playwright scraper (future phase) |
| All items filtered out | Items older than 24h | Use `--since "DD Mon YYYY 00:00"` to widen the window |
| Duplicate items appearing | First run or `--skip-dedupe` used | Normal — dedupe DB will suppress them on next regular run |

---

## 8. File layout

```
dailybrief/
├── .env                    ← secrets (gitignored, never commit)
├── .env.example            ← template to copy from
├── config/
│   ├── sources.yaml        ← RSS + HTML sources (add/remove here)
│   ├── keywords.yaml       ← high/medium/exclude keyword lists
│   └── recipients.yaml     ← email + WhatsApp recipients
├── src/dailybrief/
│   ├── main.py             ← pipeline orchestrator + CLI
│   ├── fetchers/           ← RSS, HTML (SEBI/IRDAI/NPCI), PDF
│   ├── pipeline/           ← dedupe, filter, summarizer, why_it_matters, ranker
│   ├── render/             ← HTML email, Markdown, WhatsApp message
│   └── delivery/           ← SMTP email, Twilio WhatsApp
├── templates/
│   └── email.html.j2       ← HTML email template
├── prompts/                ← Claude prompt files
├── data/
│   └── seen.db             ← dedup state (gitignored)
└── logs/
    └── dailybrief.log      ← rotating log, 5 MB × 14 files (gitignored)
```

---

## 9. Adding/removing sources

Edit `config/sources.yaml`. Each source needs:
```yaml
- id: unique_id
  type: rss              # or "html"
  url: https://...
  category: RBI          # RBI | UPI | Insurance | Markets | Banking
  priority: 1            # 1=highest, 3=lowest
  fetch_full_text: false # true = follow links and fetch PDF/HTML body (RBI only)
```

---

## 10. Enabling Claude body summaries (optional, costs money)

Set in `.env`:
```
SKIP_CLAUDE_SUMMARIZATION=false
```
Claude will generate a 1–2 sentence summary per article instead of showing the raw RSS body.
Estimated cost: ~₹0.15–₹0.50 per run depending on item count.
