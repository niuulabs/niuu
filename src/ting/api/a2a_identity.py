"""Stable local A2A discovery coordinates shared by Ting launch surfaces."""

from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH


def local_agent_card_url(*, public_base_url: str, request_base_url: str) -> str:
    """Return the exact local card URL configured in Ravn's agent directory."""
    base = public_base_url.rstrip("/") or request_base_url.rstrip("/")
    return f"{base}{AGENT_CARD_WELL_KNOWN_PATH}"
