"""Build the store a Mímir service or evaluation runs against.

A store is named the dynamic-adapter way (``.claude/rules/dynamic-adapters.md``):
a mapping with the fully-qualified :class:`~niuu.ports.mimir.MimirPort` class under
``adapter`` and its constructor arguments as the remaining keys::

    {"adapter": "mimir.adapters.gbrain.GBrainMimirAdapter",
     "mcp_url": "http://localhost:3131/mcp", "api_token_file": "/run/secrets/gbrain"}

There is no default store. A service or evaluation with none named does not start.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mimir.exceptions import MimirConfigurationError
from niuu.ports.mimir import MimirPort
from niuu.utils import import_class

_EXAMPLE = '{"adapter": "mimir.adapters.gbrain.GBrainMimirAdapter", "mcp_url": "<gbrain /mcp url>"}'


def build_backend(spec: Mapping[str, Any] | None, *, setting: str) -> MimirPort:
    """Construct the :class:`MimirPort` that *spec* names.

    *setting* is the name of the configuration knob the spec came from (for example
    ``MIMIR__BACKEND`` or ``--backend``), so a failure tells the operator what to fix.

    Raises:
        MimirConfigurationError: *spec* is missing, names no ``adapter``, names a class
            that cannot be imported, or names one that is not a ``MimirPort``.
    """
    if not spec:
        raise MimirConfigurationError(
            f"{setting} is not set. Name the store this Mímir serves as an adapter and its "
            f"arguments, for example {_EXAMPLE}."
        )
    dotted = str(spec.get("adapter") or "").strip()
    if not dotted:
        raise MimirConfigurationError(
            f"{setting} has no 'adapter'. Set 'adapter' to the fully-qualified MimirPort class, "
            f"for example {_EXAMPLE}."
        )
    try:
        cls = import_class(dotted)
    except (ImportError, AttributeError, ValueError) as exc:
        raise MimirConfigurationError(
            f"{setting} adapter {dotted!r} cannot be imported: {exc}. Fix the class path."
        ) from exc
    if not isinstance(cls, type) or not issubclass(cls, MimirPort):
        raise MimirConfigurationError(
            f"{setting} adapter {dotted!r} is not a MimirPort. Name a class that implements "
            "niuu.ports.mimir.MimirPort, such as mimir.adapters.gbrain.GBrainMimirAdapter."
        )
    kwargs = {key: value for key, value in spec.items() if key != "adapter"}
    return cls(**kwargs)
