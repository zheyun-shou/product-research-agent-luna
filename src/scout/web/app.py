"""The routes of the web chat page. Each turn streams its events as lines of JSON."""
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scout.web.service import Busy, ChatService

STATIC = Path(__file__).parent / "static"
THREAD = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
BUSY = "A turn is running. Wait until the reply is done."


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ProblemAnswer(BaseModel):
    skip: bool = False


def checked(thread: str) -> str:
    if not THREAD.match(thread):
        raise HTTPException(400, "The thread ID can have only letters, digits, - and _.")
    return thread


def create_app(service: ChatService) -> FastAPI:
    app = FastAPI(title="product-scout", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/threads/{thread}")
    def history(thread: str):
        try:
            return service.history(checked(thread))
        except Busy:
            raise HTTPException(409, BUSY)

    @app.post("/api/threads/{thread}/messages")
    def send(thread: str, message: Message):
        try:
            events = service.turn(checked(thread), message.text.strip())
        except Busy:
            raise HTTPException(409, BUSY)
        lines = (json.dumps(event, ensure_ascii=False) + "\n" for event in events)
        return StreamingResponse(lines, media_type="application/x-ndjson")

    @app.delete("/api/threads/{thread}/picks/{product_id}")
    def remove(thread: str, product_id: int):
        try:
            return service.remove(checked(thread), product_id)
        except Busy:
            raise HTTPException(409, BUSY)
        except KeyError:
            raise HTTPException(404, "This product is not a pick of the thread.")

    @app.get("/api/memory")
    def memory():
        try:
            return service.memories()
        except Busy:
            raise HTTPException(409, BUSY)

    @app.delete("/api/memory/{memory_id}")
    def forget(memory_id: int):
        try:
            service.forget(memory_id)
        except Busy:
            raise HTTPException(409, BUSY)
        except KeyError:
            raise HTTPException(404, "There is no such memory.")
        return {"ok": True}

    @app.post("/api/problem")
    def problem(answer: ProblemAnswer):
        if not service.answer_problem(answer.skip):
            raise HTTPException(404, "No turn waits for a login or a captcha.")
        return {"ok": True}

    return app
