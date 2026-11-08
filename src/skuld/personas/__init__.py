"""Persona projection: play a pinned persona on the harness a session runs.

``projection`` defines the contract; ``claude``, ``codex`` and ``instruction_only`` are the
engine families; ``registry`` maps a transport adapter to its projector. The broker projects
the session's persona on every start (``TransportLifecycleMixin._create_transport``).
"""
