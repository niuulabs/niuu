"""Tests for the shared dynamic-adapter composition helpers."""

from __future__ import annotations

import pytest

from ravn.dynamic_adapters import (
    accepts_kwarg,
    adapter_kwargs,
    build_dynamic_adapter,
    config_value,
    import_adapter,
    secret_kwargs,
)

_RECORDER_PATH = f"{__name__}.KwargRecorder"


class KwargRecorder:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class NamedOnly:
    def __init__(self, name: str = "") -> None:
        self.name = name


class TestBuildDynamicAdapter:
    def test_builds_from_dict_config(self) -> None:
        adapter = build_dynamic_adapter({"adapter": _RECORDER_PATH, "kwargs": {"a": 1}, "b": 2})

        assert isinstance(adapter, KwargRecorder)
        assert adapter.kwargs == {"a": 1, "b": 2}

    def test_missing_adapter_path_returns_none(self) -> None:
        assert build_dynamic_adapter({"kwargs": {"a": 1}}) is None
        assert build_dynamic_adapter({"adapter": "  "}) is None

    def test_extra_kwargs_offered_only_when_accepted(self) -> None:
        accepted = build_dynamic_adapter(
            {"adapter": _RECORDER_PATH},
            extra_kwargs={"store": "the-store"},
        )
        rejected = build_dynamic_adapter(
            {"adapter": f"{__name__}.NamedOnly"},
            extra_kwargs={"store": "the-store"},
        )

        assert accepted.kwargs == {"store": "the-store"}
        assert isinstance(rejected, NamedOnly)

    def test_explicit_kwargs_beat_extra_kwargs(self) -> None:
        adapter = build_dynamic_adapter(
            {"adapter": _RECORDER_PATH, "store": "explicit"},
            extra_kwargs={"store": "default"},
        )

        assert adapter.kwargs == {"store": "explicit"}

    def test_secret_kwargs_resolved_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("RAVN_TEST_TOKEN", "s3cret")
        adapter = build_dynamic_adapter(
            {
                "adapter": _RECORDER_PATH,
                "secret_kwargs_env": {"token": "RAVN_TEST_TOKEN", "missing": "RAVN_TEST_NOPE"},
            }
        )

        assert adapter.kwargs == {"token": "s3cret"}


class TestAdapterKwargs:
    def test_prefers_adapter_kwargs_method(self) -> None:
        class WithMethod:
            def adapter_kwargs(self) -> dict:
                return {"x": 1}

        assert adapter_kwargs(WithMethod()) == {"x": 1}

    def test_dict_config_drops_reserved_keys(self) -> None:
        payload = adapter_kwargs(
            {"adapter": "a.B", "secret_kwargs_env": {"k": "E"}, "kwargs": {"a": 1}, "b": 2}
        )

        assert payload == {"a": 1, "b": 2}

    def test_plain_object_uses_dunder_dict(self) -> None:
        class Plain:
            def __init__(self) -> None:
                self.adapter = "a.B"
                self.c = 3

        assert adapter_kwargs(Plain()) == {"c": 3}

    def test_pydantic_model_uses_model_dump(self) -> None:
        from pydantic import BaseModel

        class Config(BaseModel):
            adapter: str = "a.B"
            d: int = 4

        assert adapter_kwargs(Config()) == {"d": 4}


class TestSecretKwargs:
    def test_empty_when_unset(self) -> None:
        assert secret_kwargs({"secret_kwargs_env": {}}) == {}
        assert secret_kwargs({}) == {}


class TestConfigValue:
    def test_reads_dict_and_attribute_configs(self) -> None:
        assert config_value({"adapter": "x"}, "adapter", "") == "x"
        assert config_value(KwargRecorder(), "adapter", "fallback") == "fallback"


class TestImportAdapter:
    def test_imports_class_by_dotted_path(self) -> None:
        assert import_adapter(_RECORDER_PATH) is KwargRecorder

    def test_invalid_path_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid adapter path"):
            import_adapter("noclasshere")


class TestAcceptsKwarg:
    def test_var_keyword_accepts_anything(self) -> None:
        assert accepts_kwarg(KwargRecorder, "anything") is True

    def test_named_parameter(self) -> None:
        assert accepts_kwarg(NamedOnly, "name") is True
        assert accepts_kwarg(NamedOnly, "store") is False
