"""HTTP for the website assistant.

Public, like the introduction form, and gated harder than it: every message is
a paid call to Anthropic. While the assistant is a private preview for the
client, a message needs the access code; once it launches, ASSISTANT_PUBLIC
drops the code and the rate limits are what is left. The conversation itself
is app/services/assistant.py.
"""
from __future__ import annotations

import hmac
import json
from collections.abc import Iterator
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app.api.clients import client_ip
from app.api.deps import AssistantDep, SettingsDep
from app.config import Settings
from app.api.throttle import (
    assistant_code_attempts, assistant_daily, assistant_messages, assistant_site_daily,
)

router = APIRouter(prefix="/api/public/assistant", tags=["assistant"])

# What one visitor message may be. The page stops typing at the same number.
MAX_QUESTION = 2000


class AssistantStatus(BaseModel):
    enabled: bool
    code_required: bool


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    # Answers the assistant wrote come back in later requests, so they may be
    # longer than a question; this bounds what a page can claim it said.
    content: str = Field(min_length=1, max_length=6000)


class ChatRequest(BaseModel):
    # Twenty questions and their answers. A longer conversation starts again.
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _a_conversation(self) -> "ChatRequest":
        """Visitor first and last, taking turns, as the API requires — refused
        here with a 422 rather than there as a paid-for error."""
        for index, message in enumerate(self.messages):
            if message.role != ("user" if index % 2 == 0 else "assistant"):
                raise ValueError("messages must alternate, starting with the visitor")
        if self.messages[-1].role != "user":
            raise ValueError("the last message must be the visitor's")
        if len(self.messages[-1].content) > MAX_QUESTION:
            raise ValueError(f"a question can be at most {MAX_QUESTION} characters")
        return self


@router.get("", response_model=AssistantStatus)
def assistant_status(settings: SettingsDep) -> AssistantStatus:
    """Whether the page should offer the chat, and whether to ask for the code."""
    return AssistantStatus(
        enabled=settings.assistant_configured,
        code_required=not settings.assistant_public,
    )


def _admit(request: Request, settings: Settings, code: str | None) -> str | None:
    """Switched on, and the right code where one is needed. Returns the
    caller's address for the rate limits."""
    if not settings.assistant_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "The assistant is not switched on.")
    ip = client_ip(request, settings)
    if settings.assistant_public:
        return ip
    # compare_digest, so the time a wrong code takes says nothing about how
    # much of it was right.
    if not hmac.compare_digest((code or "").encode(), settings.assistant_access_code.encode()):
        if not assistant_code_attempts.check(ip):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                "Too many wrong codes. Wait a minute and try again.")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The access code is not right.")
    return ip


@router.post("/code", status_code=status.HTTP_204_NO_CONTENT)
def check_code(
    request: Request, settings: SettingsDep, x_assistant_code: str | None = Header(None),
) -> None:
    """Lets the page check a code as it is typed in, rather than finding out
    on the first question — which would cost an answer to learn it was wrong."""
    _admit(request, settings, x_assistant_code)


@router.post("/chat")
def chat(
    payload: ChatRequest, request: Request, settings: SettingsDep, assistant: AssistantDep,
    x_assistant_code: str | None = Header(None),
) -> StreamingResponse:
    ip = _admit(request, settings, x_assistant_code)

    # Counted only once the code is right, so wrong guesses cannot use up
    # somebody's allowance.
    if not (assistant_messages.check(ip) and assistant_daily.check(ip)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "That's a lot of messages. Please wait a few minutes.")
    if not assistant_site_daily.check("site"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "The assistant is resting for today. Please try again tomorrow.")

    events = assistant.reply([m.model_dump() for m in payload.messages])
    return StreamingResponse(
        _server_sent(events),
        media_type="text/event-stream",
        # Asks every proxy between here and the browser — Vercel's rewrite
        # among them — to pass each event on as it comes rather than hold the
        # answer back until it is finished.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def _server_sent(events: Iterator[dict[str, str]]) -> Iterator[str]:
    for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
