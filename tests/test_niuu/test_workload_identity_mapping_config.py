"""Tests for WorkloadIdentityMappingConfig.owner_id_claim_pattern validation.

A bad pattern used to be a 500 at exchange time (every real caller through
that mapping failing the same way, only discoverable by trying it) — this
validator fails at config-load time instead.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from niuu.config_models import WorkloadIdentityMappingConfig


def _mapping(**overrides) -> dict:
    base = {"name": "test-mapping"}
    base.update(overrides)
    return base


class TestOwnerIdClaimPatternValidation:
    def test_no_pattern_is_valid(self) -> None:
        WorkloadIdentityMappingConfig(**_mapping())  # must not raise

    def test_pattern_with_one_group_and_claim_is_valid(self) -> None:
        WorkloadIdentityMappingConfig(
            **_mapping(owner_id_claim="sub", owner_id_claim_pattern=r"^resident-(.+)$")
        )  # must not raise

    def test_pattern_without_owner_id_claim_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires owner_id_claim"):
            WorkloadIdentityMappingConfig(**_mapping(owner_id_claim_pattern=r"^resident-(.+)$"))

    def test_pattern_that_does_not_compile_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="does not compile"):
            WorkloadIdentityMappingConfig(
                **_mapping(owner_id_claim="sub", owner_id_claim_pattern=r"^resident-(.+$")
            )

    def test_pattern_with_zero_capture_groups_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exactly one capture group"):
            WorkloadIdentityMappingConfig(
                **_mapping(owner_id_claim="sub", owner_id_claim_pattern=r"^resident-.+$")
            )

    def test_pattern_with_two_capture_groups_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exactly one capture group"):
            WorkloadIdentityMappingConfig(
                **_mapping(
                    owner_id_claim="sub",
                    owner_id_claim_pattern=r"^(resident)-(.+)$",
                )
            )
