from __future__ import annotations

import uuid
from datetime import datetime, timezone


class MockEmailService:
    """Mock implementation of the EmailService protocol.

    Logs emails and stores them in a list for verification.
    """

    def __init__(self) -> None:
        self._sent_emails: list[dict] = []

    @property
    def sent_emails(self) -> list[dict]:
        """Return a copy of all sent emails for verification."""
        return list(self._sent_emails)

    async def send_email(self, to: str, subject: str, body: str) -> dict:
        """Log the email and store it. Returns dict with message_id and status."""
        message_id = f"msg-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        email_record = {
            "message_id": message_id,
            "to": to,
            "subject": subject,
            "body": body,
            "status": "delivered",
            "sent_at": now,
        }

        self._sent_emails.append(email_record)
        return {
            "message_id": message_id,
            "status": "delivered",
        }
