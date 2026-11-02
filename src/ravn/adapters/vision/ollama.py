"""Ollama multimodal vision adapter."""

from __future__ import annotations

import base64

import httpx

from ravn.ports.vision import VisionPort

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_TIMEOUT = 120.0


class OllamaVisionAdapter(VisionPort):
    """Call a locally configured Ollama multimodal model."""

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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": selected_model,
                    "stream": False,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                            "images": [base64.b64encode(image).decode("ascii") for image in images],
                        }
                    ],
                },
            )
            response.raise_for_status()

        content = response.json().get("message", {}).get("content")
        if not isinstance(content, str) or not content:
            raise ValueError("Ollama vision model returned no text content")
        return content
