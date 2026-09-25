"""Where each instance kind serves its health check."""

from __future__ import annotations

from niuu.domain.models import InstanceKind

#: Health path per instance kind, appended to ``instance.base_url``.
#:
#: Every default here is deliberately the path under that service's own API
#: prefix (``/api/v1/<service>/health``), not a bare ``/health``. Two real
#: deployment shapes break on a bare path:
#:
#: - A standalone service (e.g. an Observatory scout) whose ingress only
#:   routes its own ``/api/v1/<service>`` prefix — a bare ``/health`` never
#:   reaches the pod, so the probe reports a healthy instance as unreachable
#:   forever.
#: - A host shared with the web-next SPA, whose nginx also answers a bare
#:   ``/health`` itself (containers/niuu-web/nginx.conf) — the probe gets a
#:   200 from the frontend's static health check and reports "ok" regardless
#:   of whether the actual backend is up.
#:
#: An instance whose base_url does not follow the kind's usual convention
#: (e.g. it already ends in a version prefix) overrides this via
#: ``config.health_path`` — see ``_resolve_health_path``.
DEFAULT_HEALTH_PATHS: dict[InstanceKind, str] = {
    InstanceKind.VOLUNDR: "/api/v1/forge/health",
    InstanceKind.TING: "/api/v1/ting/health",
    InstanceKind.MIMIR: "/api/v1/mimir/health",
    InstanceKind.BIFROST: "/api/v1/bifrost/health",
    InstanceKind.RAVN: "/api/v1/ravn/health",
    InstanceKind.OBSERVATORY: "/api/v1/observatory/health",
    InstanceKind.GENERIC: "/health",
}
