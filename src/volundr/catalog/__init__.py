"""Forge catalog component — config-driven session-definitions and launch specs.

The catalog is the subset of the Forge API that has no runtime dependencies (no
database, pod manager, gateway, secret injection, or Bifrost). The full Forge
mounts it via :func:`volundr.main.create_app` through the
:func:`volundr.catalog.assembly.build_catalog` builder.
"""

from volundr.catalog.assembly import CatalogComponents, build_catalog

__all__ = ["CatalogComponents", "build_catalog"]
