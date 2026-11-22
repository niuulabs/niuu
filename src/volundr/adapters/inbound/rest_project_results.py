"""How project errors answer over HTTP."""

from __future__ import annotations

from fastapi import HTTPException

from volundr.domain.execution_catalog import ExecutionSelectionError
from volundr.domain.project_memory import (
    MemoryDirectoryUnavailableError,
    MemoryMountClashError,
)
from volundr.domain.project_ports import (
    ProjectAccessDeniedError,
    ProjectConflictError,
    ProjectMemberNotFoundError,
)
from volundr.domain.services.projects import ProjectNotFoundError
from volundr.domain.services.session import SessionAccessDeniedError


async def project_result(operation):
    try:
        return await operation
    except ProjectNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProjectConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (SessionAccessDeniedError, ProjectAccessDeniedError) as exc:
        raise HTTPException(403, str(exc)) from exc
    except ProjectMemberNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ExecutionSelectionError:
        raise
    except MemoryMountClashError as exc:
        raise HTTPException(409, str(exc)) from exc
    except MemoryDirectoryUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            503, "Project checkout is unavailable; retry after restoring it"
        ) from exc
