"""Tests for KubernetesConfigMapPersonaRegistry (NIU-642).

All k8s API calls are mocked via unittest.mock — no live cluster required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ravn.adapters.personas.loader import PersonaConfig, PersonaLLMConfig
from volundr.adapters.personas.configmap import (
    _BUILTIN_NAMES,
    KubernetesConfigMapPersonaRegistry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_YAML = """\
name: reviewer
system_prompt_template: |
  You are a code reviewer.
permission_mode: workspace-read
"""

_SAMPLE_CONFIG = PersonaConfig(
    name="reviewer",
    system_prompt_template="You are a code reviewer.\n",
    permission_mode="workspace-read",
)


def _make_registry(
    data: dict[str, str] | None = None,
) -> tuple[KubernetesConfigMapPersonaRegistry, MagicMock]:
    """Return a registry wired to a mock CoreV1Api with the given ConfigMap data."""
    mock_api = MagicMock()
    cm = MagicMock()
    cm.data = data if data is not None else {}
    mock_api.read_namespaced_config_map.return_value = cm

    registry = KubernetesConfigMapPersonaRegistry(
        namespace="volundr",
        configmap_name="ravn-personas",
        _api=mock_api,
    )
    return registry, mock_api


def _make_404_exception() -> Exception:
    """Return a mock exception that looks like a kubernetes ApiException(404)."""
    exc = Exception("Not Found")
    exc.status = 404  # type: ignore[attr-defined]
    return exc


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------


class TestLoad:
    def test_returns_persona_when_key_present(self) -> None:
        registry, _ = _make_registry({"reviewer.yaml": _SAMPLE_YAML})
        persona = registry.load("reviewer")
        assert persona is not None
        assert persona.name == "reviewer"

    def test_returns_none_when_key_absent(self) -> None:
        registry, _ = _make_registry({"other.yaml": _SAMPLE_YAML})
        assert registry.load("reviewer") is None

    def test_returns_none_for_empty_configmap(self) -> None:
        registry, _ = _make_registry({})
        assert registry.load("reviewer") is None

    def test_returns_none_when_configmap_missing(self) -> None:
        registry, mock_api = _make_registry()
        mock_api.read_namespaced_config_map.side_effect = _make_404_exception()
        assert registry.load("reviewer") is None

    def test_propagates_non_404_errors(self) -> None:
        registry, mock_api = _make_registry()
        exc = Exception("Internal Server Error")
        exc.status = 500  # type: ignore[attr-defined]
        mock_api.read_namespaced_config_map.side_effect = exc
        with pytest.raises(RuntimeError, match="Failed to read ConfigMap"):
            registry.load("reviewer")


# ---------------------------------------------------------------------------
# list_names()
# ---------------------------------------------------------------------------


class TestListNames:
    def test_returns_sorted_names(self) -> None:
        registry, _ = _make_registry(
            {
                "reviewer.yaml": _SAMPLE_YAML,
                "coordinator.yaml": _SAMPLE_YAML,
                "coder.yaml": _SAMPLE_YAML,
            }
        )
        assert registry.list_names() == ["coder", "coordinator", "reviewer"]

    def test_ignores_non_yaml_keys(self) -> None:
        registry, _ = _make_registry(
            {
                "reviewer.yaml": _SAMPLE_YAML,
                "README": "ignore me",
                "metadata.json": "{}",
            }
        )
        assert registry.list_names() == ["reviewer"]

    def test_empty_configmap(self) -> None:
        registry, _ = _make_registry({})
        assert registry.list_names() == []

    def test_missing_configmap_returns_empty(self) -> None:
        registry, mock_api = _make_registry()
        mock_api.read_namespaced_config_map.side_effect = _make_404_exception()
        assert registry.list_names() == []


# ---------------------------------------------------------------------------
# save()
# ---------------------------------------------------------------------------


class TestSave:
    def test_patches_existing_configmap(self) -> None:
        registry, mock_api = _make_registry({"existing.yaml": _SAMPLE_YAML})

        registry.save(_SAMPLE_CONFIG)

        mock_api.patch_namespaced_config_map.assert_called_once()
        call_kwargs = mock_api.patch_namespaced_config_map.call_args
        body = call_kwargs[1]["body"] if "body" in call_kwargs[1] else call_kwargs[0][2]
        assert "reviewer.yaml" in body["data"]

    def test_patches_only_changed_key(self) -> None:
        """Strategic merge patch only needs the new/updated key — other keys are
        preserved by the merge semantics, so we should NOT re-send them."""
        registry, mock_api = _make_registry({"coder.yaml": _SAMPLE_YAML})

        registry.save(_SAMPLE_CONFIG)

        call_kwargs = mock_api.patch_namespaced_config_map.call_args
        body = call_kwargs[1]["body"] if "body" in call_kwargs[1] else call_kwargs[0][2]
        # Only the saved key should be in the patch body
        assert "reviewer.yaml" in body["data"]
        assert "coder.yaml" not in body["data"]

    def test_auto_creates_configmap_when_missing(self) -> None:
        registry, mock_api = _make_registry()
        # Patch returns 404 → triggers auto-create fallback
        mock_api.patch_namespaced_config_map.side_effect = _make_404_exception()

        registry.save(_SAMPLE_CONFIG)

        mock_api.create_namespaced_config_map.assert_called_once()
        call_kwargs = mock_api.create_namespaced_config_map.call_args
        body = call_kwargs[1]["body"] if "body" in call_kwargs[1] else call_kwargs[0][1]
        # body may be a V1ConfigMap mock or dict; check via attribute or key
        if hasattr(body, "data"):
            assert "reviewer.yaml" in body.data
        else:
            assert "reviewer.yaml" in body["data"]

    def test_propagates_non_404_patch_errors(self) -> None:
        registry, mock_api = _make_registry()
        exc = Exception("Forbidden")
        exc.status = 403  # type: ignore[attr-defined]
        mock_api.patch_namespaced_config_map.side_effect = exc
        with pytest.raises(RuntimeError, match="Failed to save persona to ConfigMap"):
            registry.save(_SAMPLE_CONFIG)

    def test_propagates_patch_conflict_errors(self) -> None:
        registry, mock_api = _make_registry({"reviewer.yaml": _SAMPLE_YAML})
        mock_api.patch_namespaced_config_map.side_effect = Exception("conflict")
        with pytest.raises(RuntimeError, match="Failed to save persona to ConfigMap"):
            registry.save(_SAMPLE_CONFIG)


# ---------------------------------------------------------------------------
# delete()
# ---------------------------------------------------------------------------


class TestDelete:
    def test_returns_true_and_patches_when_key_found(self) -> None:
        registry, mock_api = _make_registry({"reviewer.yaml": _SAMPLE_YAML})

        result = registry.delete("reviewer")

        assert result is True
        mock_api.patch_namespaced_config_map.assert_called_once()
        call_kwargs = mock_api.patch_namespaced_config_map.call_args
        body = call_kwargs[1]["body"] if "body" in call_kwargs[1] else call_kwargs[0][2]
        # Null-value patch signals strategic merge patch to delete the key
        assert body["data"]["reviewer.yaml"] is None

    def test_returns_false_when_key_absent(self) -> None:
        registry, mock_api = _make_registry({"coder.yaml": _SAMPLE_YAML})
        result = registry.delete("reviewer")
        assert result is False
        mock_api.patch_namespaced_config_map.assert_not_called()

    def test_returns_false_when_configmap_missing(self) -> None:
        registry, mock_api = _make_registry()
        mock_api.read_namespaced_config_map.side_effect = _make_404_exception()
        result = registry.delete("reviewer")
        assert result is False

    def test_propagates_non_404_read_errors(self) -> None:
        registry, mock_api = _make_registry()
        exc = Exception("Server Error")
        exc.status = 500  # type: ignore[attr-defined]
        mock_api.read_namespaced_config_map.side_effect = exc
        with pytest.raises(RuntimeError, match="Failed to read ConfigMap"):
            registry.delete("reviewer")

    def test_propagates_patch_errors(self) -> None:
        registry, mock_api = _make_registry({"reviewer.yaml": _SAMPLE_YAML})
        mock_api.patch_namespaced_config_map.side_effect = Exception("conflict")
        with pytest.raises(RuntimeError, match="Failed to patch ConfigMap"):
            registry.delete("reviewer")


# ---------------------------------------------------------------------------
# is_builtin()
# ---------------------------------------------------------------------------


class TestIsBuiltin:
    def test_known_builtin_returns_true(self) -> None:
        registry, _ = _make_registry()
        assert registry.is_builtin("reviewer") is True
        assert registry.is_builtin("coordinator") is True
        assert registry.is_builtin("coder") is True

    def test_custom_persona_returns_false(self) -> None:
        registry, _ = _make_registry()
        assert registry.is_builtin("my-custom-persona") is False
        assert registry.is_builtin("") is False

    def test_all_builtin_names_are_recognized(self) -> None:
        registry, _ = _make_registry()
        for name in _BUILTIN_NAMES:
            assert registry.is_builtin(name) is True, f"Expected {name!r} to be builtin"


# ---------------------------------------------------------------------------
# load_all()
# ---------------------------------------------------------------------------


class TestLoadAll:
    def test_returns_all_valid_personas(self) -> None:
        second_yaml = "name: coder\nsystem_prompt_template: code it\n"
        registry, _ = _make_registry(
            {
                "reviewer.yaml": _SAMPLE_YAML,
                "coder.yaml": second_yaml,
            }
        )
        personas = registry.load_all()
        names = {p.name for p in personas}
        assert "reviewer" in names
        assert "coder" in names

    def test_skips_non_yaml_keys(self) -> None:
        registry, _ = _make_registry(
            {
                "reviewer.yaml": _SAMPLE_YAML,
                "README": "not a persona",
            }
        )
        personas = registry.load_all()
        assert len(personas) == 1
        assert personas[0].name == "reviewer"

    def test_skips_malformed_yaml(self) -> None:
        registry, _ = _make_registry(
            {
                "reviewer.yaml": _SAMPLE_YAML,
                "broken.yaml": ":::invalid:::yaml:::",
            }
        )
        personas = registry.load_all()
        # Only well-formed personas are returned
        assert all(p.name != "broken" for p in personas)

    def test_empty_configmap_returns_empty_list(self) -> None:
        registry, _ = _make_registry({})
        assert registry.load_all() == []


# ---------------------------------------------------------------------------
# source()
# ---------------------------------------------------------------------------


class TestSource:
    def test_returns_configmap_source_string(self) -> None:
        registry, _ = _make_registry({"reviewer.yaml": _SAMPLE_YAML})
        src = registry.source("reviewer")
        assert src == "[configmap:volundr/ravn-personas]"

    def test_returns_empty_string_when_absent(self) -> None:
        registry, _ = _make_registry({})
        assert registry.source("reviewer") == ""

    def test_returns_empty_string_when_configmap_missing(self) -> None:
        registry, mock_api = _make_registry()
        mock_api.read_namespaced_config_map.side_effect = _make_404_exception()
        assert registry.source("reviewer") == ""


# ---------------------------------------------------------------------------
# Port contract: PersonaRegistryPort compliance
# ---------------------------------------------------------------------------


class TestPersonaRegistryPortCompliance:
    def test_implements_persona_registry_port(self) -> None:
        from ravn.ports.persona import PersonaRegistryPort

        registry, _ = _make_registry()
        assert isinstance(registry, PersonaRegistryPort)

    def test_round_trip_save_then_load(self) -> None:
        """save() followed by load() recovers the same config."""
        stored: dict[str, str] = {}

        mock_api = MagicMock()

        def read_cm(**kwargs):  # noqa: ANN001
            cm = MagicMock()
            cm.data = dict(stored)
            return cm

        def patch_cm(name, namespace, body, **kwargs):  # noqa: ANN001
            stored.update(body["data"])

        mock_api.read_namespaced_config_map.side_effect = read_cm
        mock_api.patch_namespaced_config_map.side_effect = patch_cm

        registry = KubernetesConfigMapPersonaRegistry(
            namespace="default",
            configmap_name="ravn-personas",
            _api=mock_api,
        )

        config = PersonaConfig(
            name="test-persona",
            system_prompt_template="You are a test agent.",
            permission_mode="workspace-read",
            llm=PersonaLLMConfig(primary_alias="fast"),
        )

        registry.save(config)
        loaded = registry.load("test-persona")

        assert loaded is not None
        assert loaded.name == "test-persona"
        assert loaded.permission_mode == "workspace-read"
        assert loaded.llm.primary_alias == "fast"
