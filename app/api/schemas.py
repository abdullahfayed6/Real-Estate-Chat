from pydantic import BaseModel


class ChatRequest(BaseModel):
    phone: str
    message: str


class ChatResponse(BaseModel):
    reply: str
    images: list[str] = []
