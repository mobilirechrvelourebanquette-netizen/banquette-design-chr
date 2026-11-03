"""Meta WhatsApp Cloud API signature, webhook parsing, and outbound messaging."""
import hashlib
import hmac
import json
import logging
import os

import httpx

from services.queue import enqueue_job
from services.routing import build_reply, route_message

logger = logging.getLogger(__name__)


def verify_meta_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    if not app_secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def extract_messages(payload: dict) -> list[dict[str, str]]:
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
                sender = message.get("from", "")
                message_id = message.get("id", "")
                kind = message.get("type", "unknown")
                text_data = message.get("text", {})
                text = text_data.get("body", "") if isinstance(text_data, dict) else ""
                if all(isinstance(item, str) for item in (sender, message_id, kind, text)):
                    extracted.append({"sender": sender, "message_id": message_id, "type": kind, "text": text})
    return extracted


async def enqueue_meta_messages(raw_body: bytes) -> int:
    """Validate the signed JSON body, route text, and durably enqueue replies."""
    payload = json.loads(raw_body)
    if not isinstance(payload, dict):
        raise ValueError("Webhook payload root must be an object")

    queued = 0
    for message in extract_messages(payload):
        if message["type"] != "text" or not message["text"] or not message["sender"]:
            continue
        routed = await route_message(message["text"])
        response_text = build_reply(routed)
        await enqueue_job(
            "whatsapp_reply",
            {
                "recipient": message["sender"],
                "message_id": message["message_id"],
                "text": response_text,
                "language": routed.language,
                "intent": routed.intent,
            },
        )
        queued += 1
    logger.info("Queued %d WhatsApp reply job(s).", queued)
    return queued


async def send_whatsapp_text(recipient: str, text: str, message_id: str) -> None:
    """Send a text reply via Meta Graph API; retries are handled by the queue worker."""
    token = os.getenv("META_ACCESS_TOKEN", "")
    phone_number_id = os.getenv("META_PHONE_NUMBER_ID", "")
    graph_version = os.getenv("META_GRAPH_API_VERSION", "")
    if not token or not phone_number_id or not graph_version:
        raise RuntimeError("Meta send API configuration is incomplete")

    # Skip a retry when a prior attempt was confirmed successful.
    from services.queue import get_redis

    redis = await get_redis()
    dedupe_key = f"whatsapp:sent:{message_id}" if message_id else ""
    if dedupe_key and await redis.exists(dedupe_key):
        return

    url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
        response = await client.post(url, headers=headers, json=payload)
    if response.is_error:
        # Do not log provider response bodies; they can contain sensitive data.
        raise RuntimeError(f"Meta Send API returned HTTP {response.status_code}")
    if dedupe_key:
        await redis.set(dedupe_key, "1", ex=60 * 60 * 24 * 30)
