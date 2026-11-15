"""A workflow run's unit needs the mesh: its members hand steps to each other over it."""

from __future__ import annotations

from uuid import uuid4

import pytest

from volundr.adapters.outbound.mesh_settings import require_unit_mesh
from volundr.domain.ports import HostedUnitError


@pytest.mark.parametrize("mesh", [{"enabled": True}, {"enabled": "true"}, {"enabled": " True "}])
def test_an_enabled_mesh_is_accepted(mesh):
    require_unit_mesh(mesh, unit_id=uuid4(), where="the pod manager's mesh")


@pytest.mark.parametrize("mesh", [{}, {"enabled": False}, {"enabled": "false"}, {"enabled": ""}])
def test_a_mesh_that_is_off_is_refused_naming_the_unit_and_the_setting(mesh):
    unit_id = uuid4()

    with pytest.raises(HostedUnitError) as refused:
        require_unit_mesh(mesh, unit_id=unit_id, where="the pod manager's mesh")

    assert str(unit_id) in str(refused.value)
    assert "the pod manager's mesh" in str(refused.value)
    assert "`enabled: true`" in str(refused.value)
