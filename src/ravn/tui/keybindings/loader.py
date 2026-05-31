"""KeybindingLoader — auto-detect and load editor keybindings.

Reads ``~/.ravn/tui.yaml`` for configuration, then parses the user's
editor config to build a :class:`KeybindingMap`.

Config schema (``~/.ravn/tui.yaml``)::

    keybindings:
      source: vim           # vim | nvim | emacs | custom | none
                            # default: vim (auto-detected)
      config_path: null     # override auto-detected config file path
      overrides:            # always applied last — wins over everything
        ctrl+h: focus_left
        ctrl+j: focus_down
        ctrl+k: focus_up
        ctrl+l: focus_right
      disabled:             # Textual key names to suppress
        - ctrl+c

Detection order when source is ``vim``::

    $MYVIMRC → ~/.config/nvim/init.lua → ~/.config/nvim/init.vim
    → ~/.vimrc → ~/.vim/vimrc → (built-in defaults only)

Detection order when source is ``nvim``::

    ~/.config/nvim/init.lua → ~/.config/nvim/init.vim → (built-in defaults only)

Detection order when source is ``emacs``::

    ~/.emacs → ~/.emacs.d/init.el → ~/.config/emacs/init.el → (built-in defaults only)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ravn.tui.keybindings.defaults import build_default_map
from ravn.tui.keybindings.model import KeybindingMap

logger = logging.getLogger(__name__)

_TUI_CONFIG = Path.home() / ".ravn" / "tui.yaml"


@dataclass
class KeybindingConfig:
    """Parsed ``keybindings`` section from ``~/.ravn/tui.yaml``."""

    source: str = "vim"
    config_path: str | None = None
    overrides: dict[str, str] = field(default_factory=dict)
    disabled: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KeybindingConfig:
        kb = data.get("keybindings", {}) or {}
        return cls(
            source=str(kb.get("source", "vim")),
            config_path=kb.get("config_path"),
            overrides=dict(kb.get("overrides", {}) or {}),
            disabled=list(kb.get("disabled", []) or []),
        )

    @classmethod
    def load_from_file(cls, path: Path) -> KeybindingConfig:
        """Read *path* as YAML; return defaults on any failure."""
        try:
            import yaml  # type: ignore[import]

            data = yaml.safe_load(path.read_text()) or {}
            return cls.from_dict(data)
        except ImportError:
            logger.debug("PyYAML not available — using keybinding defaults")
            return cls()
        except Exception as exc:
            logger.debug("cannot read tui config %s: %s", path, exc)
            return cls()


class KeybindingLoader:
    """Loads a :class:`KeybindingMap` from the user's editor configuration.

    Call :meth:`load` to get the final map. The map includes:

    1. Built-in vim-style defaults (always present).
    2. User remaps parsed from their editor config (added on top).
    3. Explicit overrides from ``~/.ravn/tui.yaml`` (wins everything).
    4. Disabled keys removed.
    """

    def __init__(self, config: KeybindingConfig | None = None) -> None:
        if config is None:
            if _TUI_CONFIG.exists():
                config = KeybindingConfig.load_from_file(_TUI_CONFIG)
            else:
                config = KeybindingConfig()
        self._config = config

    def load(self, source: str | None = None) -> KeybindingMap:
        """Build and return the :class:`KeybindingMap`.

        Args:
            source: Override ``config.source`` for this call.
        """
        effective_source = source or self._config.source

        # Start with built-in defaults (respecting disabled list)
        kb = build_default_map(disabled=self._config.disabled)

        # Parse editor config
        added = self._parse_editor(effective_source, kb)
        if added:
            logger.info("keybindings: loaded %d remaps from %s config", added, effective_source)
        else:
            logger.debug("keybindings: no editor remaps found for source=%r", effective_source)

        # Apply explicit overrides from tui.yaml
        for textual_key, action in self._config.overrides.items():
            kb.register([textual_key], action)
            logger.debug("keybindings: override %s → %s", textual_key, action)

        if kb.warnings:
            for w in kb.warnings:
                logger.debug("keybindings: %s", w)

        return kb

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _parse_editor(self, source: str, kb: KeybindingMap) -> int:
        match source:
            case "vim":
                return self._load_vim(kb)
            case "nvim" | "neovim":
                return self._load_nvim(kb)
            case "emacs":
                return self._load_emacs(kb)
            case "none" | "custom":
                return 0
            case _:
                logger.debug("keybindings: unknown source %r — skipping", source)
                return 0
        raise AssertionError("Unreachable _parse_editor fallthrough")

    def _load_vim(self, kb: KeybindingMap) -> int:
        """Try vim config, fall back to nvim, then give up."""
        from ravn.tui.keybindings.nvim import NvimLuaParser, find_nvim_config
        from ravn.tui.keybindings.vim import VimscriptParser, find_vimrc

        config_path = self._resolved_path()

        if config_path:
            path = config_path
        else:
            path = find_vimrc() or find_nvim_config()

        if path is None:
            logger.debug("keybindings: no vim/nvim config found")
            return 0

        logger.debug("keybindings: parsing vim config %s", path)
        if path.suffix == ".lua":
            return NvimLuaParser().apply_to_map(path, kb)
        return VimscriptParser().apply_to_map(path, kb)

    def _load_nvim(self, kb: KeybindingMap) -> int:
        from ravn.tui.keybindings.nvim import NvimLuaParser, find_nvim_config

        config_path = self._resolved_path() or find_nvim_config()
        if config_path is None:
            logger.debug("keybindings: no nvim config found")
            return 0

        logger.debug("keybindings: parsing nvim config %s", config_path)
        return NvimLuaParser().apply_to_map(config_path, kb)

    def _load_emacs(self, kb: KeybindingMap) -> int:
        from ravn.tui.keybindings.emacs import EmacsParser, find_emacs_config

        config_path = self._resolved_path() or find_emacs_config()
        if config_path is None:
            logger.debug("keybindings: no emacs config found")
            return 0

        logger.debug("keybindings: parsing emacs config %s", config_path)
        return EmacsParser().apply_to_map(config_path, kb)

    def _resolved_path(self) -> Path | None:
        if self._config.config_path:
            p = Path(self._config.config_path).expanduser()
            if p.exists():
                return p
            logger.warning("keybindings: configured config_path %s not found", p)
        return None
