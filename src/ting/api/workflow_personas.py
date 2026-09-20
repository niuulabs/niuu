"""Resolve authoring dependencies from the caller's authoritative persona catalog."""

import asyncio

import httpx
from fastapi import HTTPException, Request

from niuu.domain.models import Principal
from ravn.domain.persona_document import (
    PersonaDocumentError,
    PortablePersonaCollection,
    PortablePersonaSource,
)
from ting.adapters.inbound.auth import extract_bearer_token


async def authoring_persona_source(
    request: Request, principal: Principal, persona_ids: set[str]
) -> PortablePersonaSource | None:
    """Hydrate only needed source definitions using the caller's existing connection.

    Complete workflow-scoped bundles need no registry connection. Applications
    explicitly composed without a Volundr factory use their local persona source.
    A failed configured remote lookup never falls back to bundled local personas.
    """
    if not persona_ids:
        return PortablePersonaCollection([])
    factory = getattr(request.app.state, "volundr_factory", None)
    if factory is None:
        return getattr(request.app.state, "persona_source", None)
    adapter = await factory.primary_for_principal(principal)
    if adapter is None:
        raise HTTPException(status_code=503, detail="No persona registry connection is available")
    token = extract_bearer_token(request)
    try:
        documents = await asyncio.gather(
            *(
                adapter.get_current_portable_persona(name, auth_token=token, principal=principal)
                for name in sorted(persona_ids)
            )
        )
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        raise HTTPException(
            status_code=code if code in {401, 403, 422} else 502,
            detail="Could not read the caller's portable persona definitions",
        ) from exc
    except (httpx.RequestError, NotImplementedError) as exc:
        raise HTTPException(status_code=502, detail="Persona registry request failed") from exc
    except PersonaDocumentError as exc:
        raise HTTPException(
            status_code=502,
            detail="Persona registry returned an invalid portable definition",
        ) from exc
    return PortablePersonaCollection([document for document in documents if document is not None])
