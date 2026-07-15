"""Compatibility exports for the shared audit REST adapter."""

from audit.router import (
    AuditEventResponse,
    create_audit_router,
    create_canonical_audit_router,
)

__all__ = ["AuditEventResponse", "create_audit_router", "create_canonical_audit_router"]
