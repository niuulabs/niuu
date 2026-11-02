"""Anthropic Messages vision adapter, normally routed through Bifröst."""

from __future__ import annotations

import base64

import httpx

from ravn.ports.vision import VisionPort

_DEFAULT_BASE_URL = "http://bifrost:8080"
_DEFAULT_TIMEOUT = 120.0


def _media_type(image: bytes) -> str:
    """Detect an Anthropic-supported image media type from its bytes."""
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("unsupported image format; use PNG, JPEG, GIF, or WebP")


class AnthropicVisionAdapter(VisionPort):
    """Call an Anthropic-compatible Bifröst Messages endpoint for vision."""

    def __init__(
        self,
        *,
        model: str = "",
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def analyse(self, image: bytes, prompt: str, *, model: str | None = None) -> str:
        return await self._request([image], prompt, model=model)

    async def compare(self, image_a: bytes, image_b: bytes, prompt: str) -> str:
        return await self._request([image_a, image_b], prompt)

    async def ocr(self, image: bytes) -> str:
        return await self._request(
            [image],
            "Extract all visible text exactly. Return only the extracted text.",
        )

    async def _request(self, images: list[bytes], prompt: str, *, model: str | None = None) -> str:
        selected_model = model or self._model
        if not selected_model:
            raise ValueError("a vision model is required; set vision.default_model or llm.model")

        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _media_type(image),
                    "data": base64.b64encode(image).decode("ascii"),
                },
            }
            for image in images
        ]
        content.append({"type": "text", "text": prompt})
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/v1/messages",
                json={
                    "model": selected_model,
                    "max_tokens": None,
                    "messages": [{"role": "user", "content": content}],
                },
            )
            response.raise_for_status()

        texts = [
            block["text"]
            for block in response.json().get("content", [])
            if block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        if not texts:
            raise ValueError("vision model returned no text content")
        return "\n".join(texts)
