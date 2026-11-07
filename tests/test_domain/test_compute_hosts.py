"""Registered-host domain models: states, what may be claimed, and join setup."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.compute_host_fakes import make_host
from volundr.domain.compute_hosts import (
    HostDisplayState,
    HostHealth,
    HostJoinSetup,
    HostService,
    normalize_tags,
)


@pytest.mark.parametrize(
    ("changes", "state"),
    [
        ({}, HostDisplayState.READY),
        ({"allocation_id": uuid4()}, HostDisplayState.IN_SERVICE),
        ({"health": HostHealth.PENDING}, HostDisplayState.PENDING),
        ({"health": HostHealth.QUARANTINED}, HostDisplayState.QUARANTINED),
        ({"health": HostHealth.UNREACHABLE}, HostDisplayState.UNREACHABLE),
        ({"service": HostService.DRAINING}, HostDisplayState.DRAINING),
        ({"service": HostService.OUT}, HostDisplayState.OUT_OF_SERVICE),
        (
            {"service": HostService.REMOVING, "health": HostHealth.UNREACHABLE},
            HostDisplayState.REMOVING,
        ),
        (
            {"service": HostService.DRAINING, "health": HostHealth.QUARANTINED},
            HostDisplayState.QUARANTINED,
        ),
    ],
)
def test_display_state(changes, state):
    assert make_host(**changes).display_state() is state


def test_claimable_needs_healthy_active_unheld_and_sized():
    assert make_host().claimable
    assert not make_host(slots=None).claimable
    assert not make_host(allocation_id=uuid4()).claimable
    assert not make_host(service=HostService.DRAINING).claimable
    assert not make_host(health=HostHealth.PENDING).claimable


def test_accepting_ignores_the_hold():
    assert make_host(allocation_id=uuid4()).accepting
    assert not make_host(health=HostHealth.UNREACHABLE).accepting
    assert not make_host(service=HostService.OUT).accepting


def test_tags_are_validated_deduplicated_and_sorted():
    assert make_host(tags=("b", "a", "b")).tags == ("a", "b")
    assert normalize_tags(["site:hnoss", "gpu"]) == ("gpu", "site:hnoss")
    for bad in ("", " gpu", "two words", "a,b"):
        with pytest.raises(ValueError):
            normalize_tags([bad])


def test_join_setup_files_must_be_plain_absolute_paths():
    HostJoinSetup(
        kind="k",
        authorized_key_options="restrict",
        files=({"path": "/etc/niuu/connect.conf", "content": "x", "mode": "0644"},),
    )
    for item in (
        {"path": "relative", "content": "", "mode": "0600"},
        {"path": "/with space", "content": "", "mode": "0600"},
        {"path": "/q'uote", "content": "", "mode": "0600"},
        {"path": "/no-mode", "content": ""},
    ):
        with pytest.raises(ValueError):
            HostJoinSetup(kind="k", authorized_key_options="restrict", files=(item,))
