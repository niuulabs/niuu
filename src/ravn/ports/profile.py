"""Profile port — interface for Ravn deployment profile sources."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.domain.profile import RavnProfile


class ProfilePort(ABC):
    """Abstract interface for a Ravn deployment profile source.

    A profile defines the deployment identity of a Ravn node: its name,
    location, which persona it runs, what infrastructure it connects to, and
    how it behaves operationally.  By default profiles are loaded from YAML
    files in ``~/.ravn/profiles/``, but any source that implements this port
    can be used instead.

    To implement a custom profile source::

        from ravn.ports.profile import ProfilePort
        from ravn.domain.profile import RavnProfile

        class K8sConfigMapProfileAdapter(ProfilePort):
            def __init__(self, namespace: str = "ravn") -> None:
                self._namespace = namespace

            def load(self, name: str) -> RavnProfile | None:
                # fetch from a Kubernetes ConfigMap
                ...

            def list_names(self) -> list[str]:
                # list all ConfigMap keys in the namespace
                ...

    Register it in ``ravn.yaml``::

        profile_source:
          adapter: mypackage.adapters.K8sConfigMapProfileAdapter
          kwargs:
            namespace: ravn-system
    """

    @abstractmethod
    def load(self, name: str) -> RavnProfile | None:
        """Return the named profile, or ``None`` if not found.

        Implementations should return ``None`` rather than raising when the
        profile does not exist, so callers can fall back gracefully.
        """
        raise NotImplementedError

    @abstractmethod
    def list_names(self) -> list[str]:
        """Return a sorted list of all resolvable profile names."""
        raise NotImplementedError
