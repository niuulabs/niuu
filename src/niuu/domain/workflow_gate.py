"""The durable workflow-gate frame, shared by what writes it and what reads it.

Skuld logs a gate's state to the session log each time the gate opens and each time
it is decided. Ting reads reviews back from those frames, and Forge raises and settles
the gate's notification from them, so all three agree on these values.
"""

#: Session-log frame carrying a gate's state: ``{"type": ..., "gate": {...}}``.
WORKFLOW_GATE_FRAME = "workflow_gate"

#: A gate's status until someone decides it.
WORKFLOW_GATE_PENDING = "pending"

#: The one gate mode an agent may answer. Every other mode waits for a person.
AGENT_ANSWERABLE_GATE_MODE = "automated_approval"

#: ``pendingBehavior`` of a gate that waits without telling anyone.
SILENT_GATE_BEHAVIOR = "silent"
