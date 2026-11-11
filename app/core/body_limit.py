"""Bound actual ASGI body bytes, including chunked requests, before JSON parsing."""

import asyncio

from starlette.responses import JSONResponse

from app.schemas.requests import MAX_REQUEST_BODY_BYTES
from config.settings import settings


class RequestBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/v1/chat/completions":
            await self.app(scope, receive, send)
            return
        content_length = dict(scope["headers"]).get(b"content-length")
        try:
            declared = int(content_length) if content_length is not None else None
            if declared is not None and declared < 0:
                raise ValueError
        except ValueError:
            await JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)(scope, receive, send)
            return
        if declared is not None and declared > MAX_REQUEST_BODY_BYTES:
            await JSONResponse({"detail": "Request body is too large."}, status_code=413)(scope, receive, send)
            return
        body = bytearray()

        async def read_body():
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return 400
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > MAX_REQUEST_BODY_BYTES:
                    return 413
                body.extend(chunk)
                if not message.get("more_body", False):
                    return 200

        try:
            code = await asyncio.wait_for(read_body(), settings.REQUEST_BODY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            code = 408
        if code != 200:
            detail = {400: "Incomplete request body.", 408: "Request body timed out.", 413: "Request body is too large."}[code]
            await JSONResponse({"detail": detail}, status_code=code)(scope, receive, send)
            return
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
