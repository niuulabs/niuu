"""Read a pod's log exactly as its containers wrote it."""

from __future__ import annotations

from typing import Any


async def read_pod_log(core_v1: Any, name: str, namespace: str, **kwargs: Any) -> str:
    """Return the log of pod *name* as text, byte for byte.

    ``kubernetes_asyncio`` deserialises a log body that happens to parse as
    JSON — a container writing JSON lines — and hands back ``str(dict)``
    (single quotes, ``True``/``None``), so the log no longer reads as JSON.
    Reading the raw body with ``_preload_content=False`` keeps every line as
    written. *kwargs* go to ``read_namespaced_pod_log`` (``tail_lines``,
    ``container``, ``limit_bytes``…).
    """
    response = await core_v1.read_namespaced_pod_log(
        name, namespace, _preload_content=False, **kwargs
    )
    try:
        return await response.text()
    finally:
        response.release()
