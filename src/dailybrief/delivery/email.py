"""Send the daily brief via SMTP or save it locally in dry-run mode.

DRY_RUN=true (default):  write HTML to data/archive/email_<timestamp>.html
DRY_RUN=false:           send via SMTP; on failure, archive anyway + return False
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)

_ARCHIVE_DIR = Path("data/archive")


def _archive_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _ARCHIVE_DIR / f"email_{ts}.html"


def _save_to_archive(html: str) -> Path:
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = _archive_path()
    path.write_text(html, encoding="utf-8")
    return path


def send_email(
    html: str,
    plain_text: str,
    subject: str,
    recipients: list[str],
    *,
    dry_run: bool = True,
    smtp_host: str = "",
    smtp_port: int = 587,
    smtp_user: str = "",
    smtp_pass: str = "",
    smtp_from: str = "brief-bot@company.com",
    smtp_use_tls: bool = True,
) -> bool:
    """Send or archive the brief.  Returns True on success."""
    archive_path = _save_to_archive(html)
    logger.info("Brief archived to %s", archive_path)

    if dry_run:
        print(f"[DRY RUN] Brief saved to: {archive_path}")
        return True

    # --- real send ---
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if smtp_use_tls:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            server.ehlo()
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)

        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)

        server.sendmail(smtp_from, recipients, msg.as_string())
        server.quit()
        logger.info("Email sent to %s", recipients)
        return True

    except Exception as exc:
        logger.error("SMTP send failed: %s — brief already archived at %s", exc, archive_path)
        return False
