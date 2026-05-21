"""Sleipnir — event bus for the Niuu platform.

Sleipnir provides a structured publish/subscribe event bus used across
Ravn, Ting, Volundr, and Bifrost. The contract (SleipnirEvent, port
interfaces) is transport-agnostic; adapters provide the actual delivery.
"""
