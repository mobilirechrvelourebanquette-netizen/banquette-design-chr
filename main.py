import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone

load_dotenv()

app = FastAPI(title="AI Agent Backend", version="1.0")

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
index_name = os.getenv("PINECONE_INDEX_NAME", "agent-memory")
index = pc.Index(index_name)

class ChatRequest(BaseModel):
    message: str
    user_id: str = "default_user"

@app.get("/")
def read_root():
    return {"status": "AI Agent Backend is running successfully!"}

@app.post("/chat")
def chat_with_agent(request: ChatRequest):
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful AI assistant."},
                {"role": "user", "content": request.message}
            ]
        )
        
        reply = response.choices[0].message.content
        return {"status": "success", "reply": reply}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if _name_ == "_main_":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
