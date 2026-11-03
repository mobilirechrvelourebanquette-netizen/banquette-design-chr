"""Meta WhatsApp Cloud API signature verification, parsing, queueing, and replies."""
import hashlib
import hmac
import json
import logging
import os

import httpx

from services.queue import enqueue_job, get_redis
from services.routing import build_reply, route_message

logger = logging.getLogger(__name__)


def verify_meta_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Validate Meta's X-Hub-Signature-256 over the exact request bytes."""
    if not app_secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def extract_messages(payload: dict) -> list[dict[str, str]]:
    """Extract text message fields while ignoring malformed webhook sections."""
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
                if not isinstance(message, dict) or message.get("type") != "text":
                    continue
                text_data = message.get("text", {})
                text = text_data.get("body", "") if isinstance(text_data, dict) else ""
                sender = message.get("from", "")
                message_id = message.get("id", "")
                if not all(isinstance(item, str) for item in (text, sender, message_id)):
                    continue
                # Meta sender identifiers are digit strings; reject malformed recipients.
                if not sender.isdigit() or not text.strip():
                    continue
                extracted.append({"sender": sender, "message_id": message_id, "text": text.strip()})
    return extracted


async def enqueue_meta_messages(raw_body: bytes) -> int:
    """Route inbound text and enqueue reply jobs before the webhook is acknowledged."""
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Meta webhook body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Meta webhook JSON root must be an object")

    queued = 0
    for message in extract_messages(payload):
        routed = await route_message(message["text"])
        reply = build_reply(routed)
        await enqueue_job(
            "whatsapp_reply",
            {
                "recipient": message["sender"],
                "message_id": message["message_id"],
                "text": reply,
                "language": routed.language,
                "intent": routed.intent,
            },
            dedupe_key=f"meta:inbound:{message['message_id']}" if message["message_id"] else None,
        )
        queued += 1
    logger.info("Queued %d WhatsApp reply job(s).", queued)
    return queued


async def send_whatsapp_text(recipient: str, text: str, message_id: str = "") -> None:
    """Send a text reply through Meta Graph API. Queue retries transient failures."""
    token = os.getenv("META_ACCESS_TOKEN", "")
    phone_number_id = os.getenv("META_PHONE_NUMBER_ID", "")
    api_version = os.getenv("META_GRAPH_API_VERSION", "")
    if not token or not phone_number_id or not api_version:
        raise RuntimeError("Meta Send API configuration is incomplete")

    if not recipient.isdigit() or not text:
        raise ValueError("Invalid WhatsApp recipient or message text")

    redis = await get_redis()
    dedupe_key = f"meta:outbound:sent:{message_id}" if message_id else ""
    if dedupe_key and await redis.exists(dedupe_key):
        return

    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
        response = await client.post(url, headers={"Authorization": f"Bearer {token}"}, json=payload)
    if response.is_error:
        raise RuntimeError(f"Meta Send API returned HTTP {response.status_code}")
    if dedupe_key:
        await redis.set(dedupe_key, "1", ex=60 * 60 * 24 * 30)
