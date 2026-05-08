"""WhatsApp delivery via Twilio Messaging API."""
from __future__ import annotations

import logging

try:
    from twilio.rest import Client as _TwilioClient
except ImportError:
    _TwilioClient = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


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

    *from_number* must be in E.164 with the 'whatsapp:' scheme prefix, e.g.
    'whatsapp:+14155238886' (Twilio sandbox) or a registered number.
    *recipients* are plain E.164 strings ('+91XXXXXXXXXX'); the prefix is added.

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

    if not all([account_sid, auth_token, from_number]):
        logger.error("[WhatsApp] Twilio credentials incomplete — skipping delivery")
        return False

    if _TwilioClient is None:
        logger.error("[WhatsApp] twilio package not installed — run: uv add twilio")
        return False

    client = _TwilioClient(account_sid, auth_token)
    success = True

    for to in recipients:
        to_wa = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
        try:
            msg = client.messages.create(body=message, from_=from_number, to=to_wa)
            logger.info("[WhatsApp] Sent to %s — SID: %s", to, msg.sid)
        except Exception as exc:
            logger.error("[WhatsApp] Failed to send to %s: %s", to, exc)
            success = False

    return success
