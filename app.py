from fastapi import FastAPI, Request, HTTPException
import os

app = FastAPI()

# আপনার নির্দিষ্ট WhatsApp বা যোগাযোগের নাম্বারটি এখানে সেট করা হলো
TARGET_PHONE_NUMBER = "+33759357105"

@app.get("/")
def read_root():
    return {
        "status": "Banquette Design CHR Backend is Live",
        "target_number": TARGET_PHONE_NUMBER,
        "primary_language": "French (fr-FR)",
        "secondary_language": "Bengali (bn-BD)",
        "message": "System ready to handle automatic calls with precise bilingual routing."
    }

@app.post("/webhook/")
async def handle_webhook(request: Request):
    try:
        body = await request.json()
        
        # Retell AI বা WhatsApp কল ইভেন্ট ট্র্যাক করার লজিক
        event_type = body.get("event")
        call_data = body.get("call", {})
        
        print(f"Incoming call detected for target number: {TARGET_PHONE_NUMBER}")
        print(f"Event Type: {event_type}")
        
        # ভাষা এবং অটো-রিসিভ কনফিগারেশন রেসপন্স
        return {
            "status": "success",
