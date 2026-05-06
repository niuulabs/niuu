"""Tests for niuu.mesh.cluster — cluster.yaml peer address reader."""

from __future__ import annotations

import textwrap

from niuu.mesh.cluster import read_cluster_pub_addresses


class TestReadClusterPubAddresses:
    """Tests for read_cluster_pub_addresses()."""

    def test_empty_config_returns_empty(self):
        assert read_cluster_pub_addresses([]) == []

    def test_entry_without_cluster_file_skipped(self):
        result = read_cluster_pub_addresses([{"adapter": "static"}])
        assert result == []

    def test_nonexistent_cluster_file_skipped(self, tmp_path):
        cfg = [{"cluster_file": str(tmp_path / "missing.yaml")}]
        assert read_cluster_pub_addresses(cfg) == []

    def test_reads_pub_addresses_from_cluster_yaml(self, tmp_path):
        cluster_yaml = tmp_path / "cluster.yaml"
        cluster_yaml.write_text(
            textwrap.dedent("""\
                peers:
                  - peer_id: alpha
                    pub_address: tcp://10.0.0.1:6000
                  - peer_id: beta
                    pub_address: tcp://10.0.0.2:6000
            """)
        )
        cfg = [{"cluster_file": str(cluster_yaml)}]
        result = read_cluster_pub_addresses(cfg)
        assert result == ["tcp://10.0.0.1:6000", "tcp://10.0.0.2:6000"]

    def test_peers_without_pub_address_skipped(self, tmp_path):
        cluster_yaml = tmp_path / "cluster.yaml"
        cluster_yaml.write_text(
            textwrap.dedent("""\
                peers:
                  - peer_id: alpha
                  - peer_id: beta
                    pub_address: tcp://10.0.0.2:6000
            """)
        )
        cfg = [{"cluster_file": str(cluster_yaml)}]
        result = read_cluster_pub_addresses(cfg)
        assert result == ["tcp://10.0.0.2:6000"]

    def test_multiple_cluster_files_merged(self, tmp_path):
        c1 = tmp_path / "c1.yaml"
        c1.write_text("peers:\n  - pub_address: tcp://1.0.0.1:6000\n")
        c2 = tmp_path / "c2.yaml"
        c2.write_text("peers:\n  - pub_address: tcp://2.0.0.1:6000\n")
        cfg = [
            {"cluster_file": str(c1)},
            {"cluster_file": str(c2)},
        ]
        result = read_cluster_pub_addresses(cfg)
        assert result == ["tcp://1.0.0.1:6000", "tcp://2.0.0.1:6000"]

    def test_empty_cluster_yaml_returns_empty(self, tmp_path):
        cluster_yaml = tmp_path / "cluster.yaml"
        cluster_yaml.write_text("")
        cfg = [{"cluster_file": str(cluster_yaml)}]
        assert read_cluster_pub_addresses(cfg) == []

    def test_cluster_yaml_with_no_peers_key(self, tmp_path):
        cluster_yaml = tmp_path / "cluster.yaml"
        cluster_yaml.write_text("nodes: []\n")
        cfg = [{"cluster_file": str(cluster_yaml)}]
        assert read_cluster_pub_addresses(cfg) == []

    def test_tilde_expansion_in_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        cluster_yaml = tmp_path / "cluster.yaml"
        cluster_yaml.write_text("peers:\n  - pub_address: tcp://3.0.0.1:6000\n")
        cfg = [{"cluster_file": "~/cluster.yaml"}]
        result = read_cluster_pub_addresses(cfg)
        assert result == ["tcp://3.0.0.1:6000"]

    def test_invalid_yaml_skipped_gracefully(self, tmp_path):
        cluster_yaml = tmp_path / "cluster.yaml"
        cluster_yaml.write_text(": : invalid yaml { [")
        cfg = [{"cluster_file": str(cluster_yaml)}]
        # Should not raise; bad file is skipped
        result = read_cluster_pub_addresses(cfg)
        assert result == []
