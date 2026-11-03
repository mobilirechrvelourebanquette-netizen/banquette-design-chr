import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from services.routing import RoutedMessage

logger = logging.getLogger(__name__)
MessageRouter = Callable[[str], Awaitable[RoutedMessage]]


def verify_meta_signature(
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str,
) -> bool:
    """Validate Meta's X-Hub-Signature-256 against the exact raw request body."""
    if not app_secret or not signature_header:
        return False

    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def extract_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Extract text messages from Meta's nested payload, ignoring malformed entries."""
    extracted: list[dict[str, str]] = []
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        return extracted

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        changes = entry.get("changes", [])
        if not isinstance(changes, list):
            continue

        for change in changes:
            if not isinstance(change, dict):
                continue

            value = change.get("value", {})
            if not isinstance(value, dict):
                continue

            messages = value.get("messages", [])
            if not isinstance(messages, list):
                continue

            for message in messages:
                if not isinstance(message, dict):
                    continue

                text_value = message.get("text", {})
                text = text_value.get("body", "") if isinstance(text_value, dict) else ""
                if not isinstance(text, str):
                    text = ""

                message_id = message.get("id", "")
                if not isinstance(message_id, str):
                    message_id = ""

                message_type = message.get("type", "unknown")
                if not isinstance(message_type, str):
                    message_type = "unknown"

                extracted.append(
                    {
                        "message_id": message_id,
                        "type": message_type,
                        "text": text,
                    }
                )

    return extracted


async def handle_meta_webhook(
    raw_body: bytes,
    router: MessageRouter,
) -> dict[str, str]:
    """Parse a signed Meta webhook and route text messages without logging contents."""
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Signed Meta webhook contained invalid JSON.")
        return {"status": "ok"}

    if not isinstance(payload, dict):
        logger.warning("Signed Meta webhook JSON root was not an object.")
        return {"status": "ok"}

    messages = extract_messages(payload)
    language_counts = {"fr-FR": 0, "bn-BD": 0}

    for message in messages:
        if not message["text"]:
            continue
        routed = await router(message["text"])
        language_counts[routed.language] = language_counts.get(routed.language, 0) + 1

    logger.info(
        "Processed Meta webhook: %d text message(s); locale counts=%s",
        sum(1 for message in messages if message["text"]),
        language_counts,
    )
    return {"status": "ok"}
