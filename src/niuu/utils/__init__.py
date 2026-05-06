"""Shared utility functions."""

from __future__ import annotations

import importlib
import os
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=256)
def import_class(dotted_path: str) -> type:
    """Import a class from a fully-qualified dotted path.

    Results are cached so repeated imports of the same adapter class
    (e.g. per-request provider instantiation) skip the string split
    and module lookup.
    """
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def resolve_secret_kwargs(
    kwargs: dict[str, Any],
    secret_kwargs_env: dict[str, str],
) -> dict[str, Any]:
    """Merge secret kwargs from environment variables into adapter kwargs.

    secret_kwargs_env maps kwarg names to env var names. Values from env
    vars override any same-named keys already in kwargs.
    """
    if not secret_kwargs_env:
        return kwargs
    resolved = dict(kwargs)
    for kwarg_name, env_var in secret_kwargs_env.items():
        value = os.environ.get(env_var)
        if value is not None:
            resolved[kwarg_name] = value
    return resolved
