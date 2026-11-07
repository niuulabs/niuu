"""AskUserTool — agent-intercepted tool for collecting user input.

This tool is declared to the LLM so it knows it can ask the user for
clarification.  Actual execution is intercepted by the agent loop
(``RavnAgent._intercept_ask_user``) before dispatch reaches this class.

The ``execute`` method is intentionally unreachable — it exists only to
satisfy the ``ToolPort`` interface.
"""

from __future__ import annotations

from niuu.domain.tool import ToolResult
from niuu.ports.tool import ToolPort


class AskUserTool(ToolPort):
    """Agent-intercepted tool: pauses the loop and collects user input.

    When the LLM calls ``ask_user``, the agent loop intercepts the call,
    emits a question to the output channel, waits for the user's response,
    and injects that response as the tool result before continuing.

    This tool should be added to the agent's tool list when an interactive
    ``user_input_fn`` is available.
    """

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "Ask the user a question when you need clarification or additional input. "
            "Use this when the user's intent is ambiguous or you need more information "
            "to complete a task correctly. "
            "Pauses the agent loop until the user provides an answer."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user.",
                },
            },
            "required": ["question"],
        }

    @property
    def required_permission(self) -> str:
        return "ask_user"

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        # This method should never be called — the agent loop intercepts
        # ask_user before it reaches the tool registry.
        return ToolResult(
            tool_call_id="",
            content="[ask_user was not intercepted by the agent loop]",
            is_error=True,
        )
