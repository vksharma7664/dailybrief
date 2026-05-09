"""WhatsApp delivery via Twilio Messaging API."""
from __future__ import annotations

import logging

try:
    from twilio.rest import Client as _TwilioClient
except ImportError:
    _TwilioClient = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# Minimum lengths for real Twilio credentials (SID = 34 chars, token = 32 chars).
# Placeholder values like "AC..." are far shorter.
_MIN_SID_LEN = 20
_MIN_TOKEN_LEN = 20


def _creds_configured(account_sid: str, auth_token: str, from_number: str) -> bool:
    """Return True only when all three Twilio values look like real credentials."""
    return (
        bool(account_sid) and len(account_sid) >= _MIN_SID_LEN
        and bool(auth_token) and len(auth_token) >= _MIN_TOKEN_LEN
        and bool(from_number)
    )


def _wa_number(number: str) -> str:
    """Ensure *number* has the 'whatsapp:' URI prefix."""
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


def send_whatsapp(
    message: str,
    recipients: list[str],
    *,
    account_sid: str,
    auth_token: str,
    from_number: str,
    dry_run: bool = True,
) -> bool:
    """Send *message* to each recipient number via Twilio WhatsApp.

    *from_number* is normalised to 'whatsapp:+E164' automatically.
    *recipients* are plain E.164 strings ('+91XXXXXXXXXX').

    Returns True when all messages are dispatched (or dry_run=True).
    Returns False if any send fails.
    """
    if not recipients:
        logger.info("[WhatsApp] No recipients configured — skipping")
        return True

    if dry_run:
        logger.info(
            "[WhatsApp dry-run] Would send to %d recipient(s). Preview:\n%s",
            len(recipients),
            message[:300],
        )
        return True

    if not _creds_configured(account_sid, auth_token, from_number):
        logger.warning(
            "[WhatsApp] Twilio credentials not configured (placeholder values?) — skipping delivery. "
            "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM in .env"
        )
        return False

    if _TwilioClient is None:
        logger.error("[WhatsApp] twilio package not installed — run: uv add twilio")
        return False

    client = _TwilioClient(account_sid, auth_token)
    from_wa = _wa_number(from_number)
    success = True

    for to in recipients:
        to_wa = _wa_number(to)
        try:
            msg = client.messages.create(body=message, from_=from_wa, to=to_wa)
            logger.info("[WhatsApp] Sent to %s — SID: %s", to, msg.sid)
        except Exception as exc:
            logger.error("[WhatsApp] Failed to send to %s: %s", to, exc)
            success = False

    return success
