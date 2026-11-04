"""Mímir — standalone knowledge service.

Exposes the Mímir wiki over HTTP so that Ravens, Valkyries, Pi room nodes,
and Hliðskjálf can read and write the knowledge base without filesystem access.

Usage
-----
Standalone (development)::

    python -m mimir serve --path ~/.ravn/mimir --port 7477

Co-located on the Ravn gateway::

    # In ravn CLI: listen-mimir mounts the MimirRouter on the existing server
    ravn listen-mimir

Discovery
---------
Ravens find a Mímir through their configured ``mimir.instances`` and the
service's own mount registry (``GET /mimir/registry/mounts``), which carries the
``announce_url`` as the instance's public URL.
"""
