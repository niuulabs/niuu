"""Tests for SessionSpec merging."""

from volundr.domain.models import PodSpecAdditions, SessionSpec, _deep_merge, _merge_pod_specs
from volundr.domain.ports import SessionContribution


class TestDeepMerge:
    def test_simple_merge(self):
        base = {"a": 1}
        override = {"b": 2}
        _deep_merge(base, override)
        assert base == {"a": 1, "b": 2}

    def test_nested_merge(self):
        base = {"session": {"id": "123", "name": "test"}}
        override = {"session": {"model": "claude"}}
        _deep_merge(base, override)
        assert base == {"session": {"id": "123", "name": "test", "model": "claude"}}

    def test_override_scalar(self):
        base = {"key": "old"}
        override = {"key": "new"}
        _deep_merge(base, override)
        assert base == {"key": "new"}

    def test_dict_overrides_scalar(self):
        base = {"key": "scalar"}
        override = {"key": {"nested": True}}
        _deep_merge(base, override)
        assert base == {"key": {"nested": True}}

    def test_empty_override(self):
        base = {"a": 1}
        _deep_merge(base, {})
        assert base == {"a": 1}

    def test_empty_base(self):
        base = {}
        _deep_merge(base, {"a": 1})
        assert base == {"a": 1}

    def test_mcp_servers_merge_by_name(self):
        base = {"mcpServers": [{"name": "shared", "command": "old"}, {"name": "alpha"}]}
        override = {
            "mcpServers": [
                {"name": "shared", "command": "new"},
                {"name": "beta"},
            ]
        }
        _deep_merge(base, override)
        assert base == {
            "mcpServers": [
                {"name": "shared", "command": "new"},
                {"name": "alpha"},
                {"name": "beta"},
            ]
        }

    def test_openshell_credential_mappings_merge_by_credential_name(self):
        base = {
            "openshell": {
                "credentialMappings": [
                    {
                        "credentialName": "codex-home",
                        "fileMappings": {"/sandbox/home/.codex/auth.json": "auth.json"},
                    }
                ]
            }
        }
        override = {
            "openshell": {
                "credentialMappings": [
                    {
                        "credentialName": "codex-home",
                        "fileMappings": {"/sandbox/home/.codex/config.toml": "config.toml"},
                    },
                    {
                        "credentialName": "openai",
                        "envMappings": {"OPENAI_API_KEY": "api_key"},
                    },
                ]
            }
        }

        _deep_merge(base, override)

        assert base["openshell"]["credentialMappings"] == [
            {
                "credentialName": "codex-home",
                "envMappings": {},
                "fileMappings": {
                    "/sandbox/home/.codex/auth.json": "auth.json",
                    "/sandbox/home/.codex/config.toml": "config.toml",
                },
            },
            {
                "credentialName": "openai",
                "envMappings": {"OPENAI_API_KEY": "api_key"},
                "fileMappings": {},
            },
        ]


class TestMergePodSpecs:
    def test_merge_empty(self):
        a = PodSpecAdditions()
        b = PodSpecAdditions()
        result = _merge_pod_specs(a, b)
        assert result.volumes == ()
        assert result.volume_mounts == ()
        assert result.labels == {}
        assert result.annotations == {}
        assert result.env == ()
        assert result.service_account is None

    def test_merge_volumes(self):
        a = PodSpecAdditions(volumes=({"name": "v1"},))
        b = PodSpecAdditions(volumes=({"name": "v2"},))
        result = _merge_pod_specs(a, b)
        assert result.volumes == ({"name": "v1"}, {"name": "v2"})

    def test_merge_labels(self):
        a = PodSpecAdditions(labels={"a": "1"})
        b = PodSpecAdditions(labels={"b": "2"})
        result = _merge_pod_specs(a, b)
        assert result.labels == {"a": "1", "b": "2"}

    def test_second_labels_win(self):
        a = PodSpecAdditions(labels={"key": "old"})
        b = PodSpecAdditions(labels={"key": "new"})
        result = _merge_pod_specs(a, b)
        assert result.labels == {"key": "new"}

    def test_service_account_second_wins(self):
        a = PodSpecAdditions(service_account="sa1")
        b = PodSpecAdditions(service_account="sa2")
        result = _merge_pod_specs(a, b)
        assert result.service_account == "sa2"

    def test_service_account_fallback_to_first(self):
        a = PodSpecAdditions(service_account="sa1")
        b = PodSpecAdditions()
        result = _merge_pod_specs(a, b)
        assert result.service_account == "sa1"


class TestSessionSpecMerge:
    def test_merge_empty_list(self):
        spec = SessionSpec.merge([])
        assert spec.values == {}
        assert spec.pod_spec.volumes == ()

    def test_merge_single_contribution(self):
        c = SessionContribution(
            values={"session": {"id": "123"}},
        )
        spec = SessionSpec.merge([c])
        assert spec.values == {"session": {"id": "123"}}

    def test_merge_multiple_contributions(self):
        c1 = SessionContribution(values={"session": {"id": "123"}})
        c2 = SessionContribution(values={"git": {"repoUrl": "https://example.com"}})
        c3 = SessionContribution(values={"session": {"model": "claude"}})
        spec = SessionSpec.merge([c1, c2, c3])
        assert spec.values == {
            "session": {"id": "123", "model": "claude"},
            "git": {"repoUrl": "https://example.com"},
        }

    def test_merge_with_pod_spec(self):
        c1 = SessionContribution(
            values={"a": 1},
            pod_spec=PodSpecAdditions(labels={"l1": "v1"}),
        )
        c2 = SessionContribution(
            values={"b": 2},
            pod_spec=PodSpecAdditions(labels={"l2": "v2"}),
        )
        spec = SessionSpec.merge([c1, c2])
        assert spec.values == {"a": 1, "b": 2}
        assert spec.pod_spec.labels == {"l1": "v1", "l2": "v2"}

    def test_merge_skips_none_pod_spec(self):
        c1 = SessionContribution(
            values={"a": 1},
            pod_spec=PodSpecAdditions(labels={"l1": "v1"}),
        )
        c2 = SessionContribution(values={"b": 2})
        spec = SessionSpec.merge([c1, c2])
        assert spec.pod_spec.labels == {"l1": "v1"}

    def test_merge_overlapping_values(self):
        c1 = SessionContribution(values={"podLabels": {"a": "1"}})
        c2 = SessionContribution(values={"podLabels": {"b": "2"}})
        spec = SessionSpec.merge([c1, c2])
        assert spec.values == {"podLabels": {"a": "1", "b": "2"}}

    def test_merge_concatenates_mcp_servers_by_name(self):
        c1 = SessionContribution(
            values={"mcpServers": [{"name": "linear", "command": "old"}, {"name": "github"}]}
        )
        c2 = SessionContribution(
            values={"mcpServers": [{"name": "linear", "command": "new"}, {"name": "mimir"}]}
        )
        spec = SessionSpec.merge([c1, c2])
        assert spec.values["mcpServers"] == [
            {"name": "linear", "command": "new"},
            {"name": "github"},
            {"name": "mimir"},
        ]
