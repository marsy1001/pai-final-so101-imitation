"""HTTP / WebSocket layer of the approval screen.

    GET  /            approval screen (touch display)
    GET  /big         big screen (1080p): sim frame and phase
    GET  /api/view    what both screens show (`ApprovalScreen.view`)
    WS   /ws          the view, pushed whenever it changes
    GET  /frame.jpg   latest sim frame (503 when rendering is off)
    POST /api/<op>    read, approve, correct, reject, manual, choose, prerecorded, idle

An operation not allowed in the current phase answers 409 and changes nothing.
approve / correct / reject / choose carry `session`, the `view()["session"]` the page was
showing; a request from a stale page (another run on screen now) answers 409. The
server holds no state of its own: everything is in the `ApprovalScreen`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .frames import FrameStore
from .screen import ActionRefused, ApprovalScreen
from .sources import FixedSource

STATIC = Path(__file__).parent / "static"
PUSH_INTERVAL_S = 0.1


class Session(BaseModel):
    session: str | None = None  # view()["session"] of the page that sent it


class Pair(Session):
    target: str
    destination: str


def create_app(
    screen: ApprovalScreen,
    source: FixedSource | None = None,
    frames: FrameStore | None = None,
) -> FastAPI:
    app = FastAPI(title="approval screen", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def op(fn, *args) -> dict:
        try:
            fn(*args)
        except ActionRefused as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return screen.view()

    @app.get("/")
    def approval_page() -> FileResponse:
        return FileResponse(STATIC / "approval.html")

    @app.get("/big")
    def big_page() -> FileResponse:
        return FileResponse(STATIC / "big.html")

    @app.get("/api/view")
    def view() -> dict:
        return screen.view()

    @app.post("/api/read")
    def read() -> dict:
        if source is None:
            raise HTTPException(status_code=409, detail="no understanding source")
        try:
            run_id = screen.begin_reading()
        except ActionRefused as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        try:
            source.start(run_id, screen.deliver)
        except Exception as e:  # noqa: BLE001 - the screen must not stay in 読み取り中
            screen.abort_reading(run_id, f"{type(e).__name__}: {e}")
            raise HTTPException(status_code=503, detail="understanding source failed") from e
        return screen.view()

    @app.post("/api/approve")
    def approve(body: Session) -> dict:
        return op(screen.approve, body.session)

    @app.post("/api/correct")
    def correct(pair: Pair) -> dict:
        return op(screen.correct, pair.session, pair.target, pair.destination)

    @app.post("/api/reject")
    def reject(body: Session) -> dict:
        return op(screen.reject, body.session)

    @app.post("/api/manual")
    def manual() -> dict:
        return op(screen.enter_manual)

    @app.post("/api/choose")
    def choose(pair: Pair) -> dict:
        return op(screen.choose, pair.session, pair.target, pair.destination)

    @app.post("/api/prerecorded")
    def prerecorded() -> dict:
        return op(screen.prerecorded)

    @app.post("/api/idle")
    def idle() -> dict:
        return op(screen.back_to_idle)

    @app.get("/frame.jpg")
    def frame() -> Response:
        jpeg = frames.latest() if frames is not None else None
        if jpeg is None:
            return Response(status_code=503)
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        sent = None
        try:
            while True:
                v = screen.view()
                if v != sent:
                    await socket.send_json(v)
                    sent = v
                await asyncio.sleep(PUSH_INTERVAL_S)
        except WebSocketDisconnect:
            return

    return app
