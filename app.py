import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone

load_dotenv()

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


class ChatRequest(BaseModel):
    message: str
    user_id: str = "default_user"


openai_api_key = os.getenv("OPENAI_API_KEY")
openai_client = OpenAI(api_key=openai_api_key) if openai_api_key else None

pinecone_api_key = os.getenv("PINECONE_API_KEY")
pinecone_index_name = os.getenv("PINECONE_INDEX_NAME", "agent-memory")
index = None

if pinecone_api_key:
    try:
        pc = Pinecone(api_key=pinecone_api_key)
        index = pc.Index(pinecone_index_name)
    except Exception:
        index = None


@app.get("/")
def read_root():
    return {
        "status": "Banquette Design CHR Backend is Live",
        "target_number": TARGET_PHONE_NUMBER,
        "primary_language": "French (fr-FR)",
        "secondary_language": "Bengali (bn-BD)",
        "message": "System ready to handle automatic calls with precise bilingual routing.",
    }


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "banquette-backend"}


@app.post("/webhook/")
async def handle_webhook(request: Request):
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(exc)}") from exc

    event_type = body.get("event")
    call_data = body.get("call", {})

    print(f"Incoming call detected for target number: {TARGET_PHONE_NUMBER}")
    print(f"Event Type: {event_type}")

    return {
        "status": "success",
        "event_type": event_type,
        "call": call_data,
        "target_number": TARGET_PHONE_NUMBER,
    }


@app.post("/chat")
def chat_with_agent(request: ChatRequest):
    if not openai_client:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful AI assistant."},
                {"role": "user", "content": request.message},
            ],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OpenAI call failed: {str(exc)}") from exc

    reply = response.choices[0].message.content
    return {"status": "success", "reply": reply}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
