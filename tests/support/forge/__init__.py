"""Forge tmux test harness — dependency-free core.

Exports the page object, hook server, and the fake-claude shim installer. The
``fakeagent`` module is intentionally NOT imported here so it stays pure-stdlib
and runnable under any interpreter as a standalone script.
"""

from __future__ import annotations

from tests.support.forge.broker_harness import BrokerHarness, FakeWsClient
from tests.support.forge.fakeclaude_shim import install_fake_claude
from tests.support.forge.hook_server import HookServer
from tests.support.forge.multipane import MultiPaneLayout, split_into_panes
from tests.support.forge.tmux_page import TmuxPage

__all__ = [
    "BrokerHarness",
    "FakeWsClient",
    "HookServer",
    "MultiPaneLayout",
    "TmuxPage",
    "install_fake_claude",
    "split_into_panes",
]
