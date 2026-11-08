"""The pinned persona document a caller sends with a session create.

A workflow run pins each persona it plays (id, revision, content). The session it launches
must play exactly that content, so Forge takes the document from the request instead of
looking the persona up by name, and refuses one whose content does not match what it claims.
"""

from typing import Any

from personas.domain.persona_document import (
    PersonaDocumentError,
    PortablePersonaDefinition,
    persona_revision_for_definition,
)

#: The request key that may carry the digest the sender pinned the document by.
DIGEST_KEY = "digest"

#: Revisions that name their own content (``persona_revision_for_definition``).
CONTENT_REVISION_PREFIX = "content-"


def pinned_persona_document(raw: dict[str, Any]) -> dict[str, Any]:
    """Return the validated portable document, or raise ``ValueError`` naming what is wrong.

    The document is parsed strictly (schema version, identifiers, the neutral tool
    vocabulary). Then its digest is checked against its content: a ``digest`` the sender
    pinned it by must equal the digest of the content received, and a content-addressed
    revision must be the revision of the content received.
    """
    document = dict(raw)
    pinned_digest = document.pop(DIGEST_KEY, None)
    try:
        parsed = PortablePersonaDefinition.from_dict(document)
    except PersonaDocumentError as exc:
        raise ValueError(f"persona_definition is not a valid persona document: {exc}") from exc
    if pinned_digest is not None and pinned_digest != parsed.digest:
        raise ValueError(
            f"persona_definition digest mismatch for {parsed.id}@{parsed.revision}: the "
            f"request pins {pinned_digest} but the document content is {parsed.digest}"
        )
    if parsed.revision.startswith(CONTENT_REVISION_PREFIX):
        expected = persona_revision_for_definition(parsed.definition)
        if parsed.revision != expected:
            raise ValueError(
                f"persona_definition revision {parsed.revision} does not match its content "
                f"(expected {expected}); the document was altered after it was pinned"
            )
    return parsed.to_dict()
