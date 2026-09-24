from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from app.agent.chat_handler import handle_message
from app.models.itinerary import ChatTurnResult

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("session_id", "message")
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


@router.post("/chat", response_model=ChatTurnResult)
async def chat(request: ChatRequest) -> ChatTurnResult:
    return await handle_message(request.session_id, request.message)
