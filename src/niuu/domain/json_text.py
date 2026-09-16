"""Render valid Unicode at JSON boundaries while preserving raw persisted events."""

import re
from typing import Any

_SURROGATE = re.compile(r"[\ud800-\udfff]")


def json_text_safe(value: Any) -> Any:
    """Join UTF-16 pairs and mark unmatched halves with U+FFFD, copying only changes.

    Native logs can contain escaped individual UTF-16 code units. Python accepts
    those in JSON, but UTF-8 response encoders and Swift JSONDecoder reject lone
    halves. The wire projection must not make the whole history unreadable.
    """
    if isinstance(value, str):
        if not _SURROGATE.search(value):
            return value
        return value.encode("utf-16-le", errors="surrogatepass").decode(
            "utf-16-le", errors="replace"
        )
    if isinstance(value, list):
        cleaned = [json_text_safe(item) for item in value]
        return value if all(a is b for a, b in zip(value, cleaned, strict=True)) else cleaned
    if isinstance(value, dict):
        cleaned = {json_text_safe(key): json_text_safe(item) for key, item in value.items()}
        return (
            value
            if len(cleaned) == len(value)
            and all(key in value and item is value[key] for key, item in cleaned.items())
            else cleaned
        )
    return value
