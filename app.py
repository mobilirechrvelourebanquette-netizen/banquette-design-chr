import logging
import os
import re
from contextlib import asynccontextmanager
from hmac import compare_digest

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from services.meta import enqueue_meta_messages, verify_meta_signature
from services.queue import enqueue_job, process_batch
from services.retell import handle_retell_webhook, start_outbound_call
from services.routing import build_reply, route_message

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
RETELL_WEBHOOK_TOKEN = os.getenv("RETELL_WEBHOOK_TOKEN", "")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")
TARGET_PHONE_NUMBER = os.getenv("TARGET_PHONE_NUMBER", "+33759357105")

openai_api_key = os.getenv("OPENAI_API_KEY")
openai_client = AsyncOpenAI(api_key=openai_api_key) if openai_api_key else None

app = FastAPI(
    title="Banquette Design CHR Backend",
    version="1.2.0",
    description="Bilingual French/Bengali backend for WhatsApp and Retell AI automation.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Hub-Signature-256", "X-Retell-Webhook-Token", "X-Worker-Token"],
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    user_id: str = Field(default="default_user", max_length=128)


class RetellTriggerRequest(BaseModel):
    phone_number: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    customer_message: str = Field(min_length=1, max_length=4000)
    consent_to_call: bool


def _constant_time_token_check(supplied: str | None, expected: str) -> bool:
    return bool(supplied and expected and compare_digest(supplied, expected))


def _check_worker_token(supplied: str | None) -> None:
    if not _constant_time_token_check(supplied, WORKER_TOKEN):
        raise HTTPException(status_code=401, detail="Worker authentication failed")


@app.get("/")
async def read_root():
    return {
        "status": "Banquette Design CHR Backend is Live",
        "target_number": TARGET_PHONE_NUMBER,
        "primary_language": "French (fr-FR)",
        "secondary_language": "Bengali (bn-BD)",
    }


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "banquette-backend"}


@app.get("/webhook")
@app.get("/webhook/")
async def verify_meta_webhook(request: Request):
    params = request.query_params
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if (
        params.get("hub.mode") == "subscribe"
        and token
        and META_VERIFY_TOKEN
        and challenge is not None
        and compare_digest(token, META_VERIFY_TOKEN)
    ):
        return PlainTextResponse(challenge, status_code=200)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@app.post("/webhook")
@app.post("/webhook/")
async def receive_meta_webhook(request: Request):
    raw_body = await request.body()
    if not verify_meta_signature(raw_body, request.headers.get("X-Hub-Signature-256"), META_APP_SECRET):
        logger.warning("Rejected Meta webhook with a missing or invalid signature.")
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        # Enqueue before acknowledging: queue outages return 503 so Meta can retry.
        queued = await enqueue_meta_messages(raw_body)
        return JSONResponse({"status": "ok", "queued": queued}, status_code=200)
    except Exception as exc:
        logger.error("Meta event could not be durably queued (%s).", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Webhook temporarily unavailable") from exc


@app.post("/webhook/retell")
async def receive_retell_webhook(request: Request):
    if not _constant_time_token_check(
        request.headers.get("X-Retell-Webhook-Token"), RETELL_WEBHOOK_TOKEN
    ):
        raise HTTPException(status_code=401, detail="Retell webhook authentication failed")

    try:
        payload = await request.json()
        result = await handle_retell_webhook(payload)
        return JSONResponse(result, status_code=200)
    except Exception as exc:
        logger.error("Retell webhook processing failed (%s).", type(exc).__name__)
        # This callback is informational; acknowledge failures to avoid retry storms.
        return JSONResponse({"status": "ok"}, status_code=200)


@app.post("/retell/trigger")
async def queue_retell_call(
    request: RetellTriggerRequest,
    x_retell_webhook_token: str | None = Header(default=None),
):
    """Queue a call only after an authenticated, explicit customer-consent trigger."""
    if not _constant_time_token_check(x_retell_webhook_token, RETELL_WEBHOOK_TOKEN):
        raise HTTPException(status_code=401, detail="Call trigger authentication failed")
    if not request.consent_to_call:
        raise HTTPException(status_code=400, detail="Explicit call consent is required")

    routed = await route_message(request.customer_message)
    await enqueue_job(
        "retell_call",
        {
            "phone_number": request.phone_number,
            "language": routed.language,
            "intent": routed.intent,
            "consent_to_call": True,
        },
    )
    return {"status": "queued", "language": routed.language, "intent": routed.intent}


async def _process_job(job_type: str, data: dict) -> None:
    if job_type == "whatsapp_reply":
        from services.meta import send_whatsapp_text

        await send_whatsapp_text(
            recipient=data["recipient"],
            text=data["text"],
            message_id=data["message_id"],
        )
        return

    if job_type == "retell_call":
        if data.get("consent_to_call") is not True:
            raise ValueError("Call job lacks recorded consent")
        await start_outbound_call(
            phone_number=data["phone_number"],
            language=data["language"],
            intent=data["intent"],
        )
        return

    raise ValueError("Unsupported queue job type")


@app.post("/internal/worker")
async def run_queue_worker(x_worker_token: str | None = Header(default=None)):
    """Drain a short batch; invoke this endpoint from a trusted scheduler/cron."""
    _check_worker_token(x_worker_token)
    try:
        stats = await process_batch(_process_job)
        return {"status": "ok", **stats}
    except Exception as exc:
        logger.error("Queue worker batch failed (%s).", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Queue worker temporarily unavailable") from exc


@app.post("/chat")
async def chat_with_agent(request: ChatRequest):
    if openai_client is None:
        raise HTTPException(status_code=503, detail="Chat service is not configured.")
    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the bilingual assistant for Banquette Design CHR, a commercial "
                        "furniture and 3D design business. Reply in French or Bengali according "
                        "to the user's language."
                    ),
                },
                {"role": "user", "content": request.message},
            ],
        )
    except Exception as exc:
        logger.error("OpenAI chat request failed (%s).", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Chat service temporarily unavailable") from exc
    return {"status": "success", "reply": response.choices[0].message.content or ""}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
