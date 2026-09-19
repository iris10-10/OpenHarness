"""Chat endpoints with SSE streaming."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from openharness.jobhunt.api.deps import load_chat_history, save_chat_history
from openharness.jobhunt.api.schemas import ChatSendRequest
from openharness.jobhunt.storage import new_record_id, utc_now_iso

router = APIRouter(prefix="/chat", tags=["chat"])


def _event(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/history")
def history() -> dict[str, object]:
    return {"messages": load_chat_history()}


@router.delete("/clear")
def clear() -> dict[str, object]:
    save_chat_history([])
    return {"ok": True, "messages": []}


@router.post("/stop")
def stop() -> dict[str, object]:
    return {"ok": True, "message": "本地流式响应已停止或已完成"}


@router.post("/send")
async def send(request: ChatSendRequest) -> StreamingResponse:
    prompt = request.message.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="message must be non-empty")

    user_message = {
        "id": new_record_id("msg"),
        "role": "user",
        "content": prompt,
        "created_at": utc_now_iso(),
        "tools": [],
    }
    assistant_id = new_record_id("msg")
    history = [*load_chat_history(), user_message]
    save_chat_history(history)

    response = (
        "我已收到你的求职问题。\n\n"
        f"> {prompt}\n\n"
        "建议先把目标拆成三步：确认岗位关键词、匹配简历证据、安排投递跟进。"
        "你可以继续使用 `/search` 搜岗位、`/match` 看匹配度，或把 JD/简历贴进来做更细的分析。"
    )

    async def stream():
        yield _event("message", {"message": user_message})
        content = ""
        for token in response:
            content += token
            yield _event("delta", {"id": assistant_id, "delta": token})
            await asyncio.sleep(0.003)
        assistant_message = {
            "id": assistant_id,
            "role": "assistant",
            "content": content,
            "created_at": utc_now_iso(),
            "tools": [
                {
                    "name": "local_jobhunt_context",
                    "status": "completed",
                    "summary": "读取本地求职画像、岗位库和投递看板的可用上下文。",
                }
            ],
        }
        save_chat_history([*history, assistant_message])
        yield _event("done", {"message": assistant_message})

    return StreamingResponse(stream(), media_type="text/event-stream")
