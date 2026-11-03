"""The question standard every agent sees (LEX-1597, LEX-1671).

Standard library only: the stdio Forge MCP server (``python -m skuld.forge_mcp``)
imports it and must start without pydantic. It is used in the ``notify`` tool, the
Forge MCP server instructions, the session prompt and the notify lint's warnings.
"""

QUESTION_STANDARD = """\
A question to the user is a closed question they can answer in one word or one tap: \
it names the choice ("A or B", "continue or stop", "approve X?"), gives at most two \
sentences of context, lists 2-4 options with your recommendation marked, and says what \
happens if they do not answer (the default and when it applies). Send it as kind=decision \
(they must choose) or kind=attention (they must act or unblock) with question=true and an \
ask: options with one recommended=true, and a default {option, after}. Progress, findings \
and "here is where I am" are not questions: send them as milestone or info. Never two \
questions in one notification."""
