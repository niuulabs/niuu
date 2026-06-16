"""Shared cluster.yaml peer address reader (NIU-631).

Both Ravn and Skuld read peer pub addresses from ``cluster.yaml`` files
referenced in the discovery adapters config.  This module centralises that
logic so fixes propagate to both.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("niuu.mesh.cluster")


def read_cluster_pub_addresses(adapters_config: list[dict[str, Any]]) -> list[str]:
    """Read peer pub addresses from static discovery adapter config.

    Each entry in *adapters_config* may contain either an inline ``peers`` list
    or a ``cluster_file`` key pointing to a YAML file with a ``peers`` list.
    This function collects all ``pub_address`` values found.

    Returns an empty list when no inline peers or existing cluster files are
    present.

    Parameters
    ----------
    adapters_config:
        List of adapter config dicts (each may have a ``cluster_file`` key).
    """
    addresses: list[str] = []

    for cfg in adapters_config:
        for peer in cfg.get("peers", []):
            if not isinstance(peer, dict):
                continue
            addr = peer.get("pub_address")
            if addr:
                addresses.append(addr)

        cluster_file = cfg.get("cluster_file", "")
        if not cluster_file:
            continue

        cf = Path(cluster_file).expanduser()
        if not cf.exists():
            logger.debug("cluster: %s not found, skipping", cf)
            continue

        try:
            cluster = yaml.safe_load(cf.read_text()) or {}
        except Exception as exc:
            logger.warning("cluster: failed to read %s: %s", cf, exc)
            continue

        for peer in cluster.get("peers", []):
            addr = peer.get("pub_address")
            if addr:
                addresses.append(addr)

    return addresses
