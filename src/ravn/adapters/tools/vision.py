"""Read-only tools for multimodal image analysis."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes, urljoin

import httpx

from ravn.adapters.tools.file_security import PathSecurityError, resolve_safe
from ravn.adapters.tools.web_fetch import _validate_url
from ravn.cap_telemetry import record_cap_hit
from ravn.domain.models import ToolResult
from ravn.ports.permission import PermissionPort
from ravn.ports.tool import ToolPort
from ravn.ports.vision import VisionPort

_DEFAULT_ANALYSE_PROMPT = "Describe this image in detail."
_DEFAULT_COMPARE_PROMPT = "Describe the meaningful differences between these images."
_PATH_READ_OVERAGE_BYTES = 1


class ImageSourceError(ValueError):
    """An image source could not be safely read."""


class ImageTooLargeError(ImageSourceError):
    """An image exceeds the configured safe request size."""


class _VisionToolBase(ToolPort):
    def __init__(
        self,
        vision: VisionPort,
        *,
        workspace: Path,
        max_image_bytes: int,
        allow_private_addresses: bool = False,
        timeout: float = 30.0,
        permission: PermissionPort | None = None,
    ) -> None:
        self._vision = vision
        self._workspace = workspace
        self._max_image_bytes = max_image_bytes
        self._allow_private_addresses = allow_private_addresses
        self._timeout = timeout
        self._permission = permission

    @property
    def required_permission(self) -> str:
        return "file:read"

    def required_permissions_for(self, input: dict) -> tuple[str, ...]:
        sources = (
            input.get("image_source"),
            input.get("image_a"),
            input.get("image_b"),
        )
        permissions: list[str] = []
        for source in sources:
            if source is None:
                continue
            permission = (
                "web:fetch"
                if isinstance(source, str) and source.startswith(("http://", "https://"))
                else self.required_permission
            )
            if permission not in permissions:
                permissions.append(permission)
        return tuple(permissions or [self.required_permission])

    async def _image(self, source: object) -> bytes:
        if isinstance(source, bytes):
            return self._validated_bytes(source)
        if not isinstance(source, str) or not source:
            raise ImageSourceError("image_source must be a workspace path, URL, data URI, or bytes")
        if source.startswith("data:"):
            return self._data_uri(source)
        if source.startswith(("http://", "https://")):
            return await self._url(source)
        return self._path(source)

    def _validated_bytes(self, image: bytes) -> bytes:
        if len(image) > self._max_image_bytes:
            raise ImageTooLargeError(
                f"image is {len(image)} bytes, exceeding vision.max_image_bytes="
                f"{self._max_image_bytes}; it was not sent to the model"
            )
        return image

    def _path(self, source: str) -> bytes:
        try:
            path = resolve_safe(source, self._workspace)
        except PathSecurityError as exc:
            raise ImageSourceError(str(exc)) from None
        if not path.is_file():
            raise ImageSourceError(f"image path is not a file: {source}")
        try:
            with path.open("rb") as image_file:
                image = image_file.read(self._max_image_bytes + _PATH_READ_OVERAGE_BYTES)
        except OSError as exc:
            raise ImageSourceError(f"could not read image: {exc}") from None
        return self._validated_bytes(image)

    def _data_uri(self, source: str) -> bytes:
        header, separator, encoded = source.partition(",")
        if not separator or not header.startswith("data:image/"):
            raise ImageSourceError("image data URI must use an image media type")
        if header.endswith(";base64") and len(encoded) > self._base64_limit():
            raise ImageTooLargeError(
                f"image data exceeds vision.max_image_bytes={self._max_image_bytes}; "
                "it was not sent to the model"
            )
        try:
            decoded = (
                base64.b64decode(encoded, validate=True)
                if header.endswith(";base64")
                else unquote_to_bytes(encoded)
            )
        except (binascii.Error, ValueError) as exc:
            raise ImageSourceError(f"invalid image data URI: {exc}") from None
        return self._validated_bytes(decoded)

    def _base64_limit(self) -> int:
        """Return the largest Base64 representation of the configured byte limit."""
        return ((self._max_image_bytes + 2) // 3) * 4

    async def _url(self, source: str) -> bytes:
        if self._permission is None:
            raise ImageSourceError("vision URL input requires permission enforcement")
        if not await self._permission.check("web:fetch"):
            raise ImageSourceError("Permission denied: vision URL input requires web:fetch")

        current_url = source
        visited_urls: set[str] = set()
        try:
            async with asyncio.timeout(self._timeout):
                async with httpx.AsyncClient(
                    timeout=self._timeout, follow_redirects=False
                ) as client:
                    while True:
                        if current_url in visited_urls:
                            raise ImageSourceError(
                                "could not fetch image URL: redirect cycle detected"
                            )
                        visited_urls.add(current_url)
                        url_error = _validate_url(
                            current_url,
                            allow_private_addresses=self._allow_private_addresses,
                        )
                        if url_error:
                            raise ImageSourceError(url_error)
                        async with client.stream("GET", current_url) as response:
                            if response.is_redirect:
                                location = response.headers.get("location")
                                if not location:
                                    raise ImageSourceError(
                                        "could not fetch image URL: redirect has no location"
                                    )
                                current_url = urljoin(str(response.url), location)
                                continue
                            response.raise_for_status()
                            content_length = response.headers.get("content-length")
                            if content_length and int(content_length) > self._max_image_bytes:
                                raise ImageTooLargeError(
                                    f"image is {content_length} bytes, exceeding "
                                    "vision.max_image_bytes="
                                    f"{self._max_image_bytes}; it was not sent to the model"
                                )
                            image = bytearray()
                            async for chunk in response.aiter_bytes():
                                image.extend(chunk)
                                if len(image) > self._max_image_bytes:
                                    raise ImageTooLargeError(
                                        "image exceeds vision.max_image_bytes="
                                        f"{self._max_image_bytes}; "
                                        "it was not sent to the model"
                                    )
                            return bytes(image)
        except ImageSourceError:
            raise
        except TimeoutError:
            record_cap_hit(
                "tools.web.fetch.timeout",
                "vision URL retrieval exceeded tools.web.fetch.timeout; raise it for a slower "
                "trusted source",
            )
            raise ImageSourceError(
                f"vision URL retrieval timed out after {self._timeout}s"
            ) from None
        except httpx.HTTPStatusError as exc:
            raise ImageSourceError(
                f"could not fetch image URL: HTTP {exc.response.status_code}"
            ) from None
        except httpx.RequestError:
            raise ImageSourceError("could not fetch image URL: network request failed") from None
        except ValueError:
            raise ImageSourceError("could not fetch image URL: invalid response headers") from None

    @staticmethod
    def _result(operation: str, content: str) -> ToolResult:
        return ToolResult(
            tool_call_id="",
            content=json.dumps({"operation": operation, "result": content}),
        )

    @staticmethod
    def _error(exc: Exception) -> ToolResult:
        if isinstance(exc, httpx.HTTPStatusError):
            return ToolResult(
                tool_call_id="",
                content=f"vision model request failed: HTTP {exc.response.status_code}",
                is_error=True,
            )
        if isinstance(exc, httpx.HTTPError):
            return ToolResult(
                tool_call_id="",
                content="vision model request failed; check the configured vision adapter",
                is_error=True,
            )
        return ToolResult(tool_call_id="", content=str(exc), is_error=True)


class VisionAnalyseTool(_VisionToolBase):
    @property
    def name(self) -> str:
        return "vision_analyse"

    @property
    def description(self) -> str:
        return "Analyse a workspace image, safe URL image, data URI, or browser screenshot."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "image_source": {"type": "string", "description": "Image path, URL, or data URI."},
                "prompt": {"type": "string", "description": "Question to answer about the image."},
            },
            "required": ["image_source"],
        }

    async def execute(self, input: dict) -> ToolResult:
        try:
            image = await self._image(input.get("image_source"))
            prompt = input.get("prompt") or _DEFAULT_ANALYSE_PROMPT
            if not isinstance(prompt, str):
                raise ImageSourceError("prompt must be a string")
            return self._result(self.name, await self._vision.analyse(image, prompt))
        except (ImageSourceError, httpx.HTTPError, ValueError) as exc:
            return self._error(exc)


class VisionCompareTool(_VisionToolBase):
    @property
    def name(self) -> str:
        return "vision_compare"

    @property
    def description(self) -> str:
        return "Compare two workspace images, safe URL images, data URIs, or browser screenshots."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "image_a": {"type": "string", "description": "First image path, URL, or data URI."},
                "image_b": {
                    "type": "string",
                    "description": "Second image path, URL, or data URI.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Question to answer about the difference.",
                },
            },
            "required": ["image_a", "image_b"],
        }

    async def execute(self, input: dict) -> ToolResult:
        try:
            image_a = await self._image(input.get("image_a"))
            image_b = await self._image(input.get("image_b"))
            prompt = input.get("prompt") or _DEFAULT_COMPARE_PROMPT
            if not isinstance(prompt, str):
                raise ImageSourceError("prompt must be a string")
            return self._result(self.name, await self._vision.compare(image_a, image_b, prompt))
        except (ImageSourceError, httpx.HTTPError, ValueError) as exc:
            return self._error(exc)


class VisionOcrTool(_VisionToolBase):
    @property
    def name(self) -> str:
        return "vision_ocr"

    @property
    def description(self) -> str:
        return (
            "Extract text from a workspace image, safe URL image, data URI, or browser screenshot."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "image_source": {"type": "string", "description": "Image path, URL, or data URI."},
            },
            "required": ["image_source"],
        }

    async def execute(self, input: dict) -> ToolResult:
        try:
            image = await self._image(input.get("image_source"))
            return self._result(self.name, await self._vision.ocr(image))
        except (ImageSourceError, httpx.HTTPError, ValueError) as exc:
            return self._error(exc)
