"""Helpers for config-seeded runtime instance registration."""

from __future__ import annotations

from niuu.domain.services.instances import InstanceService, InstanceValidationError


async def seed_configured_instances(
    service: InstanceService,
    seeded_instances: list,
) -> int:
    """Seed configured runtime instances into the shared registry.

    A `niuu.instances` entry is a deliberate operator configuration, not an
    optional hint — an incomplete one is a configuration error and must stop
    startup, not be silently dropped (see `.claude/rules/no-fallbacks.md`).
    """
    seeded = 0
    for index, item in enumerate(seeded_instances):
        slug = getattr(item, "slug", "").strip()
        name = getattr(item, "name", "").strip()
        base_url = getattr(item, "base_url", "").strip()
        if not slug or not name or not base_url:
            raise InstanceValidationError(
                f"niuu.instances[{index}] is incomplete "
                f"(slug={slug!r} name={name!r} base_url={base_url!r}); "
                "set all three fields, or remove the entry from configuration."
            )
        await service.upsert_seed_instance(
            instance_id=getattr(item, "id", None),
            kind=item.kind,
            slug=slug,
            name=name,
            base_url=base_url,
            visibility=item.visibility,
            owner_id=getattr(item, "owner_id", None),
            tenant_id=getattr(item, "tenant_id", None),
            enabled=getattr(item, "enabled", True),
            is_default=getattr(item, "is_default", False),
            config=getattr(item, "config", {}) or {},
            tags=getattr(item, "tags", []) or [],
        )
        seeded += 1
    return seeded
