"""Tests for FileSecretInjectionAdapter."""

import pytest

from volundr.adapters.outbound.file_secret_injection import FileSecretInjectionAdapter


class TestFileSecretInjectionAdapter:
    @pytest.fixture
    def adapter(self, tmp_path):
        return FileSecretInjectionAdapter(base_dir=str(tmp_path))

    async def test_pod_spec_returns_hostpath_volume(self, adapter, tmp_path):
        result = await adapter.pod_spec_additions("user-1", "sess-123")

        assert len(result.volumes) == 1
        vol = result.volumes[0]
        assert vol["name"] == "secrets-sess-123"
        assert vol["hostPath"]["path"] == f"{tmp_path}/user/user-1"
        assert vol["hostPath"]["type"] == "DirectoryOrCreate"

    async def test_pod_spec_returns_volume_mount(self, adapter):
        result = await adapter.pod_spec_additions("user-1", "sess-123")

        assert len(result.volume_mounts) == 1
        mount = result.volume_mounts[0]
        assert mount["name"] == "secrets-sess-123"
        assert mount["mountPath"] == "/run/secrets/user"
        assert mount["readOnly"] is True

    async def test_ensure_spc_is_noop(self, adapter):
        await adapter.ensure_secret_provider_class("user-1", ["cred-a", "cred-b"])

    async def test_provision_user_is_noop(self, adapter):
        await adapter.provision_user("user-1")

    async def test_deprovision_user_is_noop(self, adapter):
        await adapter.deprovision_user("user-1")

    def test_accepts_extra_kwargs(self, tmp_path):
        adapter = FileSecretInjectionAdapter(base_dir=str(tmp_path), unknown="ignored")
        assert adapter is not None


async def test_git_token_projection_and_cleanup(tmp_path):
    import json
    from pathlib import Path

    from volundr.domain.models import CredentialMapping

    source = tmp_path / "user" / "user-1" / "github"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"token": "test-token"}))
    adapter = FileSecretInjectionAdapter(base_dir=str(tmp_path))
    await adapter.ensure_secret_provider_class(
        "user-1",
        [
            CredentialMapping(
                credential_name="github",
                file_mappings={
                    "/run/secrets/git/selected/token": "token",
                },
            )
        ],
        session_id="session-1",
    )
    additions = await adapter.pod_spec_additions("user-1", "session-1")
    projected = additions.volumes[1]["hostPath"]["path"]
    assert Path(projected).read_text() == "test-token"
    assert additions.volume_mounts[1]["mountPath"] == "/run/secrets/git/selected/token"
    assert additions.volume_mounts[1]["readOnly"]
    await adapter.cleanup_session("session-1")
    assert not Path(projected).exists()
    assert source.exists()
