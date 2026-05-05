"""Tests for legacy cli.main compatibility."""

from __future__ import annotations

from unittest.mock import patch


class TestCliMainCompatibility:
    """Tests for the compatibility wrapper in cli.main."""

    def test_main_delegates_to_niuu_package_entrypoint(self) -> None:
        with patch("niuu.__main__.main") as niuu_main:
            from cli.main import main

            main()

        niuu_main.assert_called_once_with()
