"""How a Claude session signs in: the values of Skuld's ``claude_auth`` setting."""

from __future__ import annotations

from typing import Literal

ClaudeAuthMode = Literal["subscription", "api_key"]
