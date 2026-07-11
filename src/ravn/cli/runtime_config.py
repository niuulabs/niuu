"""Runtime logging and effective-configuration diagnostics for Ravn CLI modes."""

from __future__ import annotations

import logging
import os

from ravn.config import Settings

logger = logging.getLogger(__name__)


def _configure_logging(settings: Settings) -> None:
    """Apply logging config from settings."""
    level = getattr(logging, settings.logging.level.upper(), logging.WARNING)
    fmt = (
        "%(asctime)s %(name)s %(levelname)s %(message)s"
        if settings.logging.format == "text"
        else "%(message)s"
    )
    logging.basicConfig(level=level, format=fmt, force=True)


def _log_effective_config(settings: Settings) -> None:
    """Emit an INFO log with the effective config for drift detection."""
    source = os.environ.get("RAVN_CONFIG", "defaults")
    persona = settings.runtime_persona
    llm_alias = settings.effective_model()
    thinking = settings.llm.extended_thinking.enabled
    budget = settings.llm.extended_thinking.budget_tokens
    logger.info(
        "ravn effective config: persona=%s llm_alias=%s thinking=%s budget=%d source=%s",
        persona,
        llm_alias,
        thinking,
        budget,
        source,
    )
