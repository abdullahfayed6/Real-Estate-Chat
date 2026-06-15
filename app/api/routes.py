from fastapi import APIRouter

from app.core.config import logger
from app.api.schemas import ChatRequest, ChatResponse
from app.services.chat_service import process_message

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Receive {"phone","message"} and return {"reply": "...", "images": [...]}."""
    logger.info("Message from %s: %s", req.phone, req.message)
    reply, images = process_message(req.phone, req.message)
    return {"reply": reply, "images": images}

@router.get("/")
async def health():
    return {"status": "ok", "service": "realestate-chat-api"}
