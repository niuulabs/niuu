"""Shared skill-content factory for the Ravn test suite.

The metadata block is a real contract: resident_learning parses ``capability``,
``safety_class``, and ``tool_entry_point`` out of this markdown when installing
and running a skill. One factory keeps every test on the canonical shape.
"""

from __future__ import annotations


def probe_skill_content(
    skill_name: str,
    *,
    capability: str = "inspect.kubernetes.pod.oomkilled",
    safety_class: str = "read_only",
) -> str:
    """Skill markdown in the canonical resident-built probe shape."""
    return (
        f"# skill: {skill_name}\n\n"
        "metadata:\n"
        f"  capability: {capability}\n"
        "  source: valkyrie-dream-cycle\n"
        f"  safety_class: {safety_class}\n"
        "  tool_entry_point: run\n\n"
        "## Procedure\n\n"
        "1. Run the installed probe implementation.\n"
    )
