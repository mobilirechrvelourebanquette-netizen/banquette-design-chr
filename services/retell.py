"""Retell event routing and consent-gated outbound call initiation."""
import logging
import os
import re
from typing import Any

import httpx

from services.routing import route_message

logger = logging.getLogger(__name__)
_E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


async def handle_retell_webhook(
    payload: Any,
    target_phone_number: str | None = None,
) -> dict[str, Any]:
    """Classify available Retell transcript text using shared bilingual routing."""
    if not isinstance(payload, dict):
        return {"status": "ok"}
    call = payload.get("call", {})
    if not isinstance(call, dict):
        call = {}
    transcript = call.get("transcript", "")
    if not isinstance(transcript, str) or not transcript.strip():
        return {"status": "ok"}

    routed = await route_message(transcript)
    event_type = payload.get("event", "unknown")
    if not isinstance(event_type, str):
        event_type = "unknown"
    logger.info("Routed Retell event %r (locale=%s, intent=%s).", event_type, routed.language, routed.intent)
    return {
        "status": "success",
        "event_type": event_type,
        "target_phone_number": target_phone_number or "",
        "language": routed.language,
        "intent": routed.intent,
    }


async def start_outbound_call(
    phone_number: str,
    language: str,
    intent: str,
    consent_to_call: bool,
) -> dict[str, Any]:
    """Initiate a Retell call only when explicit customer consent is recorded."""
    if consent_to_call is not True:
        raise ValueError("Explicit consent is required before placing an outbound call")
    if not _E164_PATTERN.fullmatch(phone_number):
        raise ValueError("Phone number must be in E.164 format")
    if language not in {"fr-FR", "bn-BD"}:
        raise ValueError("Unsupported Retell language")

    api_key = os.getenv("RETELL_API_KEY", "")
    from_number = os.getenv("RETELL_FROM_NUMBER", "")
    agent_env = "RETELL_AGENT_ID_BN" if language == "bn-BD" else "RETELL_AGENT_ID_FR"
    agent_id = os.getenv(agent_env, "")
    if not api_key or not from_number or not agent_id:
        raise RuntimeError("Retell outbound call configuration is incomplete")

    api_base = os.getenv("RETELL_API_BASE_URL", "https://api.retellai.com").rstrip("/")
    payload = {
        "from_number": from_number,
        "to_number": phone_number,
        "override_agent_id": agent_id,
        "retell_llm_dynamic_variables": {
            "language": language,
            "inquiry_intent": intent,
        },
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=3.0)) as client:
        response = await client.post(
            f"{api_base}/v2/create-phone-call",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if response.is_error:
        raise RuntimeError(f"Retell API returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        return {"status": "accepted"}
