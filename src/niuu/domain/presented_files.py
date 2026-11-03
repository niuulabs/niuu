"""Wire names shared by the broker, the facade and Forge for durable presented files (F2)."""

#: A present-file id: ``pf_`` and 32 hex digits (the broker mints it).
PRESENTED_FILE_ID_PATTERN = r"^pf_[0-9a-f]{32}$"

#: Forge's default cap on one durable presented file (``presented_files.max_bytes``).
PRESENTED_FILE_DEFAULT_MAX_BYTES = 50 * 1024 * 1024

#: Forge's answer when a durable copy would exceed the host's total quota (LEX-1514):
#: HTTP 507 with ``detail.code`` set to this. The broker fails ``present-file`` with
#: ``detail.message``; the refusal is final, never retried.
PRESENTED_FILES_QUOTA_STATUS = 507
PRESENTED_FILES_QUOTA_CODE = "presented_files_quota"

#: The header carrying a presented file's display name on the durable upload (URL-quoted).
PRESENTED_FILE_NAME_HEADER = "x-presented-file-name"

#: A presented file is agent-supplied: browsers render it and never run it on Forge's origin
#: (HTML or SVG script, sniffed types). Every route that serves one sends these, the public
#: facade included (judge R16-1).
PRESENTED_FILE_SAFETY_HEADERS = {
    "x-content-type-options": "nosniff",
    "content-security-policy": "sandbox",
}

#: Types the sandbox would break and that cannot run script on Forge's origin: Chrome refuses
#: to show a PDF in its viewer under ``Content-Security-Policy: sandbox``.
SANDBOX_EXEMPT_TYPES = frozenset({"application/pdf"})


def presented_file_safety_headers(media_type: str | None) -> dict[str, str]:
    """The safety policy for a presented file of ``media_type``: ``nosniff`` always, and the
    sandbox for every type but the exempt ones (judge R16-1)."""
    base = (media_type or "").split(";")[0].strip().lower()
    if base in SANDBOX_EXEMPT_TYPES:
        return {"x-content-type-options": "nosniff"}
    return dict(PRESENTED_FILE_SAFETY_HEADERS)


#: The durable copy never changes under its id (a different file under the same id is a 409),
#: so a browser may keep it for a day; a viewer whose access ends keeps it that long. The live
#: broker's copy is served only while the session runs, so it is never cached.
PRESENTED_FILE_DURABLE_CACHE = "private, max-age=86400, immutable"
PRESENTED_FILE_LIVE_CACHE = "private, no-store"
PRESENTED_FILE_CACHE_POLICIES = frozenset({PRESENTED_FILE_DURABLE_CACHE, PRESENTED_FILE_LIVE_CACHE})
