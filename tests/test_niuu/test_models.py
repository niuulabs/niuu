"""Tests for niuu.domain.models."""

from __future__ import annotations

from niuu.domain.models import IntegrationType


class TestIntegrationType:
    def test_existing_source_control(self):
        assert IntegrationType.SOURCE_CONTROL == "source_control"

    def test_existing_issue_tracker(self):
        assert IntegrationType.ISSUE_TRACKER == "issue_tracker"

    def test_existing_messaging(self):
        assert IntegrationType.MESSAGING == "messaging"

    def test_existing_ai_provider(self):
        assert IntegrationType.AI_PROVIDER == "ai_provider"

    def test_code_forge_exists(self):
        assert IntegrationType.CODE_FORGE == "code_forge"

    def test_messaging_round_trip(self):
        assert IntegrationType("messaging") is IntegrationType.MESSAGING

    def test_code_forge_round_trip(self):
        assert IntegrationType("code_forge") is IntegrationType.CODE_FORGE

    def test_source_control_round_trip(self):
        assert IntegrationType("source_control") is IntegrationType.SOURCE_CONTROL

    def test_issue_tracker_round_trip(self):
        assert IntegrationType("issue_tracker") is IntegrationType.ISSUE_TRACKER

    def test_all_values_present(self):
        values = {m.value for m in IntegrationType}
        assert values >= {
            "source_control",
            "issue_tracker",
            "messaging",
            "ai_provider",
            "code_forge",
        }

    def test_code_forge_string_representation(self):
        assert str(IntegrationType.CODE_FORGE) == "code_forge"

    def test_messaging_string_representation(self):
        assert str(IntegrationType.MESSAGING) == "messaging"
