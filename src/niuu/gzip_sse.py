"""SSE-safe gzip middleware (wire compression for the big JSON payloads).

Conversation windows are large, highly repetitive JSON — the lexi-frontend-presentation
session's 15-turn shallow window measured 1.5 MB raw and 359 KB gzipped (4.3x). Clients
already negotiate (URLSession/browsers send Accept-Encoding: gzip and decompress
transparently); the server just never compressed.

SSE-SAFE: deflate buffers small chunks, which delays/starves text/event-stream consumers —
an SSE request (identified by its Accept header, which EventSource always sends) bypasses
compression entirely. `minimum_size` keeps tiny control responses uncompressed.
"""

from fastapi.middleware.gzip import GZipMiddleware


class SSESafeGZipMiddleware(GZipMiddleware):
    async def __call__(self, scope, receive, send):  # type: ignore[override]
        if scope["type"] == "http":
            accept = next(
                (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"accept"),
                "",
            )
            if "text/event-stream" in accept:
                await self.app(scope, receive, send)
                return
        await super().__call__(scope, receive, send)
