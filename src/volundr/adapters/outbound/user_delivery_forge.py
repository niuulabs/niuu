"""Per-principal strict delivery facade over configured source-control integrations."""

from __future__ import annotations

from niuu.domain.delivery import (
    BranchPublicationReceipt,
    BranchPublicationRequest,
    CheckReceipt,
    MergeReceipt,
    MergeRequest,
    PublicationSource,
    ResolvedRef,
    ReviewCandidate,
    ReviewPublication,
    ReviewRequest,
)
from niuu.domain.models import Principal
from niuu.ports.delivery import DeliveryForgeProvider
from volundr.domain.services.user_integration import UserIntegrationService


class UserDeliveryForgeProvider(DeliveryForgeProvider):
    """Resolve credentials fresh for each operation under one authenticated principal."""

    def __init__(self, integrations: UserIntegrationService, principal: Principal) -> None:
        if not principal.user_id or not principal.tenant_id:
            raise ValueError("Delivery forge operations require user and tenant identity")
        self._integrations = integrations
        self._principal = principal

    async def resolve_ref(self, repository: str, ref: str) -> ResolvedRef:
        provider = await self._provider(repository)
        return await provider.resolve_ref(repository, ref)

    async def ensure_review(self, request: ReviewRequest) -> ReviewPublication:
        provider = await self._provider(request.repository)
        return await provider.ensure_review(request)

    async def publish_branch(
        self,
        source: PublicationSource,
        request: BranchPublicationRequest,
    ) -> BranchPublicationReceipt:
        provider = await self._provider(request.repository)
        return await provider.publish_branch(source, request)

    async def inspect_delivery_candidate(
        self,
        repo_url: str,
        review_number: int,
        required_checks: tuple[str, ...],
    ) -> tuple[ReviewCandidate, CheckReceipt]:
        provider = await self._provider(repo_url)
        return await provider.inspect_delivery_candidate(repo_url, review_number, required_checks)

    async def conditional_merge(self, request: MergeRequest) -> MergeReceipt:
        provider = await self._provider(request.repository)
        return await provider.conditional_merge(request)

    async def reconcile_merge(self, request: MergeRequest) -> MergeReceipt:
        provider = await self._provider(request.repository)
        return await provider.reconcile_merge(request)

    async def _provider(self, repository: str) -> DeliveryForgeProvider:
        provider = await self._integrations.find_git_provider_for(
            repository,
            self._principal.user_id,
        )
        if provider is None:
            raise ValueError("No source-control integration supports this repository")
        if not isinstance(provider, DeliveryForgeProvider):
            raise ValueError("Configured source-control integration lacks strict delivery support")
        return provider
