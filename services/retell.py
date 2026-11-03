import logging
from typing import Any

from services.routing import detect_language

logger = logging.getLogger(__name__)


async def handle_retell_webhook(
    payload: Any,
    target_phone_number: str | None = None,
) -> dict[str, Any]:
    """Normalize Retell event metadata without blocking on external network calls."""
    if not isinstance(payload, dict):
        logger.warning("Retell webhook payload was not a JSON object.")
        return {"status": "ok"}

    event_type = payload.get("event", "unknown")
    if not isinstance(event_type, str):
        event_type = "unknown"

    call = payload.get("call", {})
    if not isinstance(call, dict):
        call = {}

    transcript = call.get("transcript", "")
    if not isinstance(transcript, str):
        transcript = ""

    language = detect_language(transcript) if transcript else "unknown"

    logger.info("Received Retell event %r (language=%s).", event_type, language)
    return {
        "status": "success",
        "event_type": event_type,
        "target_phone_number": target_phone_number or "",
        "language": language,
    }
