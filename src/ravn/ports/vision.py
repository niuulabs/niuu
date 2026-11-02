"""Vision inference port for Ravn tools."""

from __future__ import annotations

from typing import Protocol


class VisionPort(Protocol):
    """Analyse image bytes with a configured multimodal model."""

    async def analyse(self, image: bytes, prompt: str, *, model: str | None = None) -> str:
        """Return the model's analysis of one image."""

    async def compare(self, image_a: bytes, image_b: bytes, prompt: str) -> str:
        """Return the model's comparison of two images."""

    async def ocr(self, image: bytes) -> str:
        """Return text extracted from an image."""
