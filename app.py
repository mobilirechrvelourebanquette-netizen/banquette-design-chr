import logging
import os
from hmac import compare_digest

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from openai import AsyncOpenAI
from pydantic import BaseModel

from services.meta import handle_meta_webhook, verify_meta_signature
from services.retell import handle_retell_webhook
from services.routing import build_reply, route_message

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
RETELL_WEBHOOK_TOKEN = os.getenv("RETELL_WEBHOOK_TOKEN", "")
TARGET_PHONE_NUMBER = os.getenv("TARGET_PHONE_NUMBER", "+33759357105")

openai_api_key = os.getenv("OPENAI_API_KEY")
openai_client = AsyncOpenAI(api_key=openai_api_key) if openai_api_key else None

app = FastAPI(
    title="Banquette Design CHR Backend",
    version="1.1.0",
    description="Bilingual French/Bengali backend for messaging and voice integrations.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    user_id: str = "default_user"


@app.get("/")
async def read_root():
    return {
        "status": "Banquette Design CHR Backend is Live",
        "target_number": TARGET_PHONE_NUMBER,
        "primary_language": "French (fr-FR)",
        "secondary_language": "Bengali (bn-BD)",
        "message": "System ready to handle bilingual messaging and call routing.",
    }


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "banquette-backend"}


@app.get("/webhook")
@app.get("/webhook/")
async def verify_meta_webhook(request: Request):
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
        and compare_digest(token, META_VERIFY_TOKEN)
    ):
        return PlainTextResponse(challenge, status_code=200)

    logger.warning("Rejected Meta webhook verification request.")
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@app.post("/webhook")
@app.post("/webhook/")
async def receive_meta_webhook(request: Request):
    """Verify and process Meta WhatsApp webhooks; acknowledge processing failures."""
    try:
        raw_body = await request.body()
        if not verify_meta_signature(
            raw_body,
            request.headers.get("X-Hub-Signature-256"),
            META_APP_SECRET,
        ):
            logger.warning("Rejected Meta webhook with a missing or invalid signature.")
            return JSONResponse({"status": "ok"}, status_code=200)

        result = await handle_meta_webhook(raw_body, route_message)
        return JSONResponse(result, status_code=200)
    except Exception as exc:
        logger.exception("Meta webhook processing failed (%s).", type(exc).__name__)
        return JSONResponse({"status": "ok"}, status_code=200)


@app.post("/webhook/retell")
async def receive_retell_webhook(request: Request):
    """Handle Retell events separately from Meta's signed webhook endpoint."""
    token = request.headers.get("X-Retell-Webhook-Token")
    if RETELL_WEBHOOK_TOKEN and token and compare_digest(token, RETELL_WEBHOOK_TOKEN):
        pass
    else:
        logger.warning("Rejected Retell webhook: token is missing, invalid, or not configured.")
        return JSONResponse({"status": "ok"}, status_code=200)

    try:
        payload = await request.json()
        result = await handle_retell_webhook(payload, TARGET_PHONE_NUMBER)
        return JSONResponse(result, status_code=200)
    except Exception as exc:
        logger.exception("Retell webhook processing failed (%s).", type(exc).__name__)
        return JSONResponse({"status": "ok"}, status_code=200)


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
                        "You are the bilingual assistant for Banquette Design CHR, "
                        "a commercial furniture and 3D design business. Reply in French "
                        "or Bengali according to the user's language."
                    ),
                },
                {"role": "user", "content": request.message},
            ],
        )
    except Exception as exc:
        logger.exception("OpenAI chat request failed (%s).", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Chat service temporarily unavailable.") from exc

    reply = response.choices[0].message.content
    return {"status": "success", "reply": reply or ""}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
