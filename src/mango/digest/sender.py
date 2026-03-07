"""Resend API email sender."""
from __future__ import annotations

import resend

from ..config import AppConfig


def send_email(
    html_body: str,
    plain_body: str,
    config: AppConfig,
    subject_override: str | None = None,
) -> str:
    """
    Send the digest via Resend. Returns the message ID on success.
    Raises on failure (caller logs and handles).
    """
    resend.api_key = config.resend_api_key

    from datetime import datetime, timezone

    date_str = datetime.now(timezone.utc).strftime("%B %-d, %Y")
    subject = subject_override or config.digest.subject.format(date=date_str)

    params: resend.Emails.SendParams = {
        "from": config.digest.email_from,
        "to": [config.digest.email_to],
        "subject": subject,
        "html": html_body,
        "text": plain_body,
    }

    response = resend.Emails.send(params)
    return response.get("id", "")
