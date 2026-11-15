"""The status of a workflow gate that nobody has decided yet.

Ting's step engine owns workflow gates; this is the value its board reads a gate's
undecided state by.
"""

#: A gate's status until someone decides it.
WORKFLOW_GATE_PENDING = "pending"
