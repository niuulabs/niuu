"""Authenticated personal storage management on this Forge cluster."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import Principal
from volundr.domain.ports import HomeStorageBusyError, StoragePort


def create_user_storage_router(storage: StoragePort) -> APIRouter:
    router = APIRouter(prefix="/api/v1/forge", tags=["User Storage"])

    @router.get("/storage/home")
    @router.delete("/storage/home")
    async def user_home(
        request: Request,
        path: str = Query(default=""),
        principal: Principal = Depends(extract_principal),
    ) -> dict:
        operation = "delete" if request.method == "DELETE" else "list"
        try:
            result = await storage.manage_user_home(principal.user_id, operation, path)
        except NotImplementedError as exc:
            raise HTTPException(501, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (ValueError, NotADirectoryError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except HomeStorageBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (RuntimeError, TimeoutError) as exc:
            raise HTTPException(503, str(exc) or "Home storage operation timed out") from exc
        if operation == "delete" and result.get("status") != "ready":
            raise HTTPException(409, result.get("detail", "Storage is preparing; retry deletion"))
        return result

    return router
