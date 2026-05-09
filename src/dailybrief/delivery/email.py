"""Send the daily brief (or a health-alert) via SMTP; archive a copy locally.

DRY_RUN=true (default):  write HTML to data/archive/email_<timestamp>.html
DRY_RUN=false:           send via SMTP; on failure, archive anyway + return False

send_email  → tuple[bool, Path]   (success, archive_path)
send_health_alert → bool          always plain-text, sent even if brief is dry-run
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


def _smtp_connect(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    smtp_use_tls: bool,
) -> smtplib.SMTP:
    """Open and authenticate an SMTP connection."""
    if smtp_use_tls:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.ehlo()
        server.starttls()
    else:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
    if smtp_user and smtp_pass:
        server.login(smtp_user, smtp_pass)
    return server


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
) -> tuple[bool, Path]:
    """Send or archive the brief.

    Returns (success, archive_path).  The HTML is always written to the
    archive regardless of dry_run / SMTP failure so nothing is lost.
    """
    archive_path = _save_to_archive(html)
    logger.info("Brief archived to %s", archive_path)

    if dry_run:
        logger.info("[DRY RUN] Brief saved to: %s — SMTP skipped", archive_path)
        return True, archive_path

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        server = _smtp_connect(smtp_host, smtp_port, smtp_user, smtp_pass, smtp_use_tls)
        server.sendmail(smtp_from, recipients, msg.as_string())
        server.quit()
        logger.info("Email sent to %s", recipients)
        return True, archive_path
    except Exception as exc:
        logger.error("SMTP send failed: %s — brief archived at %s", exc, archive_path)
        return False, archive_path


def send_health_alert(
    failed_sources: list[tuple[str, str]],
    recipients: list[str],
    *,
    smtp_host: str = "",
    smtp_port: int = 587,
    smtp_user: str = "",
    smtp_pass: str = "",
    smtp_from: str = "brief-bot@company.com",
    smtp_use_tls: bool = True,
) -> bool:
    """Email a plain-text alert listing every source that failed this run.

    Called regardless of dry_run — this is a health/ops alert, not the brief.
    Silently returns False (with a log) if SMTP is not configured.
    """
    if not failed_sources:
        return True

    if not smtp_host:
        logger.warning(
            "[HEALTH ALERT] %d source(s) failed but SMTP not configured — "
            "alert not sent: %s",
            len(failed_sources),
            [s for s, _ in failed_sources],
        )
        return False

    ist_ts = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    subject = f"[ALERT] DailyBrief source failures – {ist_ts}"

    lines = [
        f"DailyBrief health alert — {len(failed_sources)} source(s) failed at {ist_ts}",
        "",
        "Failed sources:",
    ]
    for source_id, error in failed_sources:
        lines.append(f"  • {source_id}: {error}")
    lines += ["", "Check logs/ for the full traceback.", "— DailyBrief bot"]

    body = "\n".join(lines)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = ", ".join(recipients)

    try:
        server = _smtp_connect(smtp_host, smtp_port, smtp_user, smtp_pass, smtp_use_tls)
        server.sendmail(smtp_from, recipients, msg.as_string())
        server.quit()
        logger.info("[HEALTH ALERT] Sent to %s — %d failed source(s)", recipients, len(failed_sources))
        return True
    except Exception as exc:
        logger.error("[HEALTH ALERT] SMTP failed: %s — alert not delivered", exc)
        return False
