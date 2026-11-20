"""The contract between the platform and the session brokers it serves (LEX-1957).

A session broker keeps running the release it was launched from: an API-only
promotion restarts Forge, not the brokers. When a release changes what Forge
expects of a broker (fields it records on a turn, frames it projects), a broker
launched from an older release silently lacks it. Each such change raises
``BROKER_CONTRACT``; a broker reports its level on ``/health`` and Forge's
``/sessions/{id}/runtime-version`` compares it with its own, so a promotion can
list, and restart, every session still below.

A broker that reports no level predates this module and is below level 1.
"""

BROKER_CONTRACT = 2

# Why each level exists: what a broker below it gets wrong.
BROKER_CONTRACT_LEVELS = {
    1: (
        "records each message's sender on the stored turn and its frames (LEX-1910), "
        "refuses input while a native control is open (LEX-1936) and offers a permission "
        "card the live menu's rows (LEX-1950); a broker below it stores no sender, so "
        "the apps show 'Unknown sender'"
    ),
    2: (
        "closes a native-control card the CLI has moved past: question cards are swept, "
        "and a later main-agent hook for another tool call supersedes a card (LEX-1979); "
        "a broker below it can refuse every message for hours behind a stale card"
    ),
}


def contract_state(reported: object) -> str:
    """``met``, or ``below`` for a broker under this release's level or reporting none."""
    if isinstance(reported, int) and not isinstance(reported, bool) and reported >= BROKER_CONTRACT:
        return "met"
    return "below"
