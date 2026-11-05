import hashlib
import hmac
import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

app = FastAPI(
    title="Banquette Design CHR Backend",
    version="1.1.0",
    description="FastAPI backend for bilingual contact automation and AI agent routing.",
)

DEFAULT_ALLOWED_ORIGINS = [
    "https://banquette-design-chr-di91-five.vercel.app",
    "https://banquette-design-chr.vercel.app",
]
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", ",".join(DEFAULT_ALLOWED_ORIGINS)).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-KEY", "X-Hub-Signature-256"],
)

TARGET_PHONE_NUMBER = os.getenv("TARGET_PHONE_NUMBER", "+33759357105")
API_KEY_NAME = "X-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)
openai_api_key = os.getenv("OPENAI_API_KEY")
openai_client = OpenAI(api_key=openai_api_key) if openai_api_key else None


class ChatRequest(BaseModel):
    message: str
    user_id: str = "default_user"


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    expected_key = os.getenv("INTERNAL_API_KEY")
    if not expected_key:
        raise HTTPException(status_code=503, detail="Chat authentication is not configured.")
    if not api_key or not hmac.compare_digest(api_key, expected_key):
        raise HTTPException(status_code=403, detail="Unauthorized request")
    return api_key


@app.get("/")
async def root():
    return {"message": "Banquette Design CHR API is running."}


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "banquette-backend"}


@app.get("/api/meta-webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    verify_token = os.getenv("META_VERIFY_TOKEN")

    if not verify_token:
        raise HTTPException(status_code=503, detail="Webhook verification is not configured.")
    if mode == "subscribe" and token and hmac.compare_digest(token, verify_token) and challenge is not None:
        return Response(content=challenge, status_code=200, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Webhook verification failed.")


@app.post("/api/meta-webhook")
async def receive_webhook(request: Request):
    app_secret = os.getenv("META_APP_SECRET")
    if not app_secret:
        raise HTTPException(status_code=503, detail="Webhook signature verification is not configured.")

    raw_body = await request.body()
    supplied_signature = request.headers.get("X-Hub-Signature-256", "")
    expected_signature = "sha256=" + hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise HTTPException(status_code=403, detail="Invalid webhook signature.")

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload.")

    # Process validated Meta/WhatsApp events here. Acknowledge only after validation.
    return {"status": "success"}


@app.post("/chat")
def chat_with_agent(request: ChatRequest, api_key: str = Depends(verify_api_key)):
    if not openai_client:
        raise HTTPException(status_code=503, detail="Chat service is not configured.")

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful AI assistant."},
                {"role": "user", "content": request.message},
            ],
        )
    except Exception as exc:
        # Do not expose provider details or credentials to API clients.
        print(f"OpenAI request failed: {type(exc).__name__}")
        raise HTTPException(status_code=502, detail="Chat service request failed.") from exc

    return {"status": "success", "reply": response.choices[0].message.content}
