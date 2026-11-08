"""Request validation for the launch-spec REST bodies."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from volundr.adapters.inbound.rest_launch_specs import LaunchSpecCreate, LaunchSpecUpdate

_REMOVED = (
    "workload type ravn_flock was removed: workflow steps will run on harness sessions (NIU-1198)"
)


def test_create_refuses_the_removed_ravn_flock_workload_type() -> None:
    with pytest.raises(ValidationError) as caught:
        LaunchSpecCreate(name="flock", workload_type="ravn_flock")

    assert _REMOVED in str(caught.value)


def test_update_refuses_the_removed_ravn_flock_workload_type() -> None:
    with pytest.raises(ValidationError) as caught:
        LaunchSpecUpdate(workload_type="ravn_flock")

    assert _REMOVED in str(caught.value)


def test_update_without_a_workload_type_is_accepted() -> None:
    assert LaunchSpecUpdate(description="new").workload_type is None


def test_create_accepts_a_surviving_workload_type() -> None:
    assert LaunchSpecCreate(name="batch", workload_type="batch").workload_type == "batch"
