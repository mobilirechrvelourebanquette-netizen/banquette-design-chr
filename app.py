import hmac
import json
import logging
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from openai import AsyncOpenAI
from pinecone import Pinecone
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Banquette Design CHR Backend",
    version="1.0.0",
    description="FastAPI backend for bilingual contact automation and AI agent routing.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TARGET_PHONE_NUMBER = os.getenv("TARGET_PHONE_NUMBER", "+33759357105")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")


class ChatRequest(BaseModel):
    message: str
    user_id: str = "default_user"


openai_api_key = os.getenv("OPENAI_API_KEY")
openai_client = AsyncOpenAI(api_key=openai_api_key) if openai_api_key else None

pinecone_api_key = os.getenv("PINECONE_API_KEY")
pinecone_index_name = os.getenv("PINECONE_INDEX_NAME", "agent-memory")
index = None

if pinecone_api_key:
    try:
        pc = Pinecone(api_key=pinecone_api_key)
        index = pc.Index(pinecone_index_name)
    except Exception as exc:
        # Do not log exception text because SDK errors can contain request details.
        logger.error("Pinecone initialization failed (%s); continuing without the index.", type(exc).__name__)


def verify_meta_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Fail closed unless the Meta signature matches the configured app secret."""
    if not META_APP_SECRET or not signature_header:
        return False
    expected = "sha256=" + hmac.new(
        META_APP_SECRET.encode("utf-8"), raw_body, digestmod="sha256"
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def choose_language(text: str) -> str:
    """Route Bengali-script messages to bn-BD; use French as the default locale."""
    if any("\u0980" <= character <= "\u09ff" for character in text):
        return "bn-BD"
    return "fr-FR"


def parse_whatsapp_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Extract safe-to-process WhatsApp message fields from Meta's nested payload."""
    parsed: list[dict[str, str]] = []
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        return parsed

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
                message_type = message.get("type", "unknown")
                message_type = message_type if isinstance(message_type, str) else "unknown"
                text_obj = message.get("text", {})
                text = text_obj.get("body", "") if isinstance(text_obj, dict) else ""
                text = text if isinstance(text, str) else ""
                parsed.append(
                    {
                        "type": message_type,
                        "language": choose_language(text) if text else "unknown",
                    }
                )
    return parsed


async def process_meta_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse and route message metadata without blocking on external network calls."""
    messages = parse_whatsapp_messages(payload)
    language_counts = {"fr-FR": 0, "bn-BD": 0, "unknown": 0}
    for message in messages:
        language_counts[message["language"]] += 1
    logger.info(
        "Accepted Meta WhatsApp webhook: %d message(s); locale counts=%s",
        len(messages),
        language_counts,
    )
    # Meta expects a quick acknowledgement. No outbound WhatsApp API call is made here.
    return {"status": "ok"}


async def process_retell_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve the existing Retell/call-event acknowledgement and target routing."""
    event_type = payload.get("event")
    call_data = payload.get("call", {})
    if not isinstance(call_data, dict):
        call_data = {}
    logger.info("Received call event %r for target number %s.", event_type, TARGET_PHONE_NUMBER)
    return {
        "status": "success",
        "event_type": event_type,
        "call": call_data,
        "target_number": TARGET_PHONE_NUMBER,
    }


@app.get("/")
async def read_root():
    return {
        "status": "Banquette Design CHR Backend is Live",
        "target_number": TARGET_PHONE_NUMBER,
        "primary_language": "French (fr-FR)",
        "secondary_language": "Bengali (bn-BD)",
        "message": "System ready to handle automatic calls with precise bilingual routing.",
    }


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "banquette-backend"}


@app.get("/webhook")
@app.get("/webhook/")
async def verify_webhook(request: Request):
    """Handle Meta's GET subscription verification handshake."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if (
        mode == "subscribe"
        and META_VERIFY_TOKEN
        and token
        and challenge is not None
        and hmac.compare_digest(token, META_VERIFY_TOKEN)
    ):
        return PlainTextResponse(challenge, status_code=200)

    logger.warning("Rejected webhook verification request.")
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@app.post("/webhook")
@app.post("/webhook/")
async def handle_webhook(request: Request):
    """Acknowledge Meta events safely and retain the existing Retell event route."""
    try:
        raw_body = await request.body()
        if not raw_body:
            logger.warning("Received an empty webhook request body; acknowledging it.")
            return JSONResponse({"status": "ok"}, status_code=200)

        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("Invalid webhook JSON (%s); acknowledging without processing.", type(exc).__name__)
            return JSONResponse({"status": "ok"}, status_code=200)

        if not isinstance(payload, dict):
            logger.warning("Webhook JSON was not an object; acknowledging without processing.")
            return JSONResponse({"status": "ok"}, status_code=200)

        is_meta = payload.get("object") == "whatsapp_business_account" or "entry" in payload
        if is_meta:
            signature = request.headers.get("X-Hub-Signature-256")
            if not verify_meta_signature(raw_body, signature):
                # Acknowledge but never process unauthenticated Meta content, avoiding retry loops.
                logger.warning("Meta webhook signature invalid or META_APP_SECRET is unset; payload ignored.")
                return JSONResponse({"status": "ok"}, status_code=200)
            result = await process_meta_webhook(payload)
            return JSONResponse(result, status_code=200)

        # Retell has its own webhook authentication scheme; preserve the existing trigger
        # acknowledgement here rather than incorrectly applying Meta's HMAC scheme to it.
        result = await process_retell_webhook(payload)
        return JSONResponse(result, status_code=200)

    except Exception as exc:
        # Catch unexpected parsing/processing failures; log only the exception class to avoid
        # exposing message contents, credentials, or other request data in logs.
        logger.error("Webhook processing failed (%s); acknowledging request.", type(exc).__name__)
        return JSONResponse({"status": "ok"}, status_code=200)


@app.post("/chat")
async def chat_with_agent(request: ChatRequest):
    if not openai_client:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant for Banquette Design CHR. Reply in French or Bengali according to the user's language.",
                },
                {"role": "user", "content": request.message},
            ],
        )
    except Exception as exc:
        logger.error("OpenAI chat request failed (%s).", type(exc).__name__)
        raise HTTPException(status_code=502, detail="OpenAI call failed.") from exc

    reply = response.choices[0].message.content
    return {"status": "success", "reply": reply}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
