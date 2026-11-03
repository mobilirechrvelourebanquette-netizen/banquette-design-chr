"""Retell webhook processing and consent-gated outbound call integration."""
import logging
import os
import re

import httpx

from services.routing import route_message

logger = logging.getLogger(__name__)
E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


async def handle_retell_webhook(payload) -> dict[str, str]:
    """Route available Retell transcript context to the same bilingual inquiry layer."""
    if not isinstance(payload, dict):
        return {"status": "ok"}
    call = payload.get("call", {})
    if not isinstance(call, dict):
        call = {}
    transcript = call.get("transcript", "")
    if not isinstance(transcript, str) or not transcript.strip():
        return {"status": "ok"}

    routed = await route_message(transcript)
    event = payload.get("event", "unknown")
    logger.info("Routed Retell event %r to locale=%s intent=%s.", event, routed.language, routed.intent)
    return {"status": "ok", "language": routed.language, "intent": routed.intent}


async def start_outbound_call(phone_number: str, language: str, intent: str) -> dict:
    """Create a Retell call after the caller has explicitly consented to a callback."""
    if not E164_PATTERN.fullmatch(phone_number):
        raise ValueError("Phone number must be in E.164 format")

    api_key = os.getenv("RETELL_API_KEY", "")
    from_number = os.getenv("RETELL_FROM_NUMBER", "")
    if not api_key or not from_number:
        raise RuntimeError("Retell outbound call configuration is incomplete")

    agent_id = os.getenv("RETELL_AGENT_ID_BN" if language == "bn-BD" else "RETELL_AGENT_ID_FR", "")
    if not agent_id:
        raise RuntimeError("Retell agent ID for the routed language is not configured")

    base_url = os.getenv("RETELL_API_BASE_URL", "https://api.retellai.com").rstrip("/")
    payload = {
        "from_number": from_number,
        "to_number": phone_number,
        "override_agent_id": agent_id,
        "retell_llm_dynamic_variables": {"language": language, "inquiry_intent": intent},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=3.0)) as client:
        response = await client.post(
            f"{base_url}/v2/create-phone-call",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if response.is_error:
        raise RuntimeError(f"Retell API returned HTTP {response.status_code}")
    return response.json()
