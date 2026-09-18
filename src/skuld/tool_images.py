"""Metadata-only access to image blocks returned by coding tools.

Claude Read envelopes, Anthropic/MCP image blocks and Codex data URLs share the
same preview contract. Text containing filenames or image-shaped code is not an
image event. No base64 decoding or network IO happens in this module.
"""

from __future__ import annotations

import json
from typing import Any


def image_payloads(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            return []
    if isinstance(content, dict) and content.get("type") == "image":
        file = content.get("file")
        if isinstance(file, dict):
            dims = file.get("dimensions") or {}
            return [
                {
                    "data": file.get("base64"),
                    "mime_type": file.get("type"),
                    "img_w": dims.get("displayWidth") if isinstance(dims, dict) else None,
                    "img_h": dims.get("displayHeight") if isinstance(dims, dict) else None,
                }
            ]
    blocks = content if isinstance(content, list) else [content]
    images = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "image":
            source = block.get("source")
            if isinstance(source, dict):
                images.append({"data": source.get("data"), "mime_type": source.get("media_type")})
            elif isinstance(block.get("data"), str):
                images.append({"data": block["data"], "mime_type": block.get("mimeType")})
        if (
            kind in {"image", "input_image", "output_image", "image_url", "Image"}
            and not block.get("source")
            and not block.get("data")
        ):
            url = block.get("image_url") or block.get("imageUrl")
            if isinstance(url, dict):
                url = url.get("url")
            if not isinstance(url, str) or not url.startswith("data:image/"):
                continue
            header, separator, data = url.partition(",")
            if separator and header.endswith(";base64"):
                images.append({"data": data, "mime_type": header[5:-7]})
    return images


def image_metadata(content: Any) -> list[dict[str, Any]]:
    """Only small, serializable hints may enter shallow history."""
    metadata = []
    for index, image in enumerate(image_payloads(content)):
        item = {"index": index}
        mime = image.get("mime_type")
        if isinstance(mime, str) and mime.startswith("image/"):
            item["mime_type"] = mime
        for key in ("img_w", "img_h"):
            value = image.get(key)
            if (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and 0 < value < float("inf")
            ):
                item[key] = int(value)
        metadata.append(item)
    return metadata
