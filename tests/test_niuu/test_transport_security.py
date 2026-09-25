"""Tests for the shared LAN-transport policy, including the trusted-plaintext
host-suffix exemption (config-first alongside localhost) that lets an
in-cluster Kubernetes service address like
http://niuu-volundr.volundr.svc.cluster.local skip the https-unless-
allow_plaintext requirement without an operator setting allow_plaintext on
every seed.
"""

from __future__ import annotations

import pytest

from niuu.domain import transport_security
from niuu.domain.transport_security import (
    _DEFAULT_TRUSTED_PLAINTEXT_HOST_SUFFIXES,
    _matches_trusted_plaintext_suffix,
    configure_trusted_plaintext_host_suffixes,
    default_trusted_plaintext_host_suffixes,
    insecure_transport_reason,
)


@pytest.fixture(autouse=True)
def _reset_trusted_plaintext_suffixes():
    """Every test starts from, and restores, the real module default —
    other test modules (and production code) rely on this default being in
    effect unless a test explicitly reconfigures it."""
    configure_trusted_plaintext_host_suffixes(_DEFAULT_TRUSTED_PLAINTEXT_HOST_SUFFIXES)
    yield
    configure_trusted_plaintext_host_suffixes(_DEFAULT_TRUSTED_PLAINTEXT_HOST_SUFFIXES)


class TestSuffixMatching:
    def test_a_label_boundary_match_is_trusted(self) -> None:
        assert _matches_trusted_plaintext_suffix(
            "niuu-volundr.volundr.svc.cluster.local", [".svc.cluster.local"]
        )

    def test_a_mid_label_substring_is_not_trusted(self) -> None:
        """notreallyasvc.cluster.local ends with 'svc.cluster.local' as a
        raw substring, but there is no dot immediately before 'svc' — it is
        not the label boundary '.svc.cluster.local' requires."""
        assert not _matches_trusted_plaintext_suffix(
            "notreallyasvc.cluster.local", [".svc.cluster.local"]
        )

    def test_a_suffix_appearing_mid_hostname_is_not_trusted(self) -> None:
        """The suffix must be the actual tail of the hostname, not merely
        appear somewhere inside it — this host's real tail is
        '.example.com'."""
        assert not _matches_trusted_plaintext_suffix(
            "evil-svc.cluster.local.example.com", [".svc.cluster.local"]
        )

    def test_matching_is_case_insensitive(self) -> None:
        assert _matches_trusted_plaintext_suffix(
            "Niuu-Volundr.Volundr.SVC.CLUSTER.LOCAL", [".svc.cluster.local"]
        )

    def test_a_trailing_dot_absolute_fqdn_still_matches(self) -> None:
        assert _matches_trusted_plaintext_suffix(
            "niuu-volundr.volundr.svc.cluster.local.", [".svc.cluster.local"]
        )

    def test_a_configured_suffix_without_a_leading_dot_is_normalized(self) -> None:
        """An operator who writes 'svc.cluster.local' (no leading dot) still
        gets label-boundary matching, not an accidental substring match."""
        assert _matches_trusted_plaintext_suffix(
            "niuu-volundr.volundr.svc.cluster.local", ["svc.cluster.local"]
        )
        assert not _matches_trusted_plaintext_suffix(
            "notreallyasvc.cluster.local", ["svc.cluster.local"]
        )

    def test_the_shorter_svc_suffix_also_matches(self) -> None:
        assert _matches_trusted_plaintext_suffix("niuu-volundr.volundr.svc", [".svc"])

    def test_an_empty_suffix_list_matches_nothing(self) -> None:
        assert not _matches_trusted_plaintext_suffix("niuu-volundr.volundr.svc.cluster.local", [])

    def test_a_blank_configured_suffix_is_ignored_not_matched_as_empty(self) -> None:
        assert not _matches_trusted_plaintext_suffix("anything.example.com", ["", "   "])


class TestInsecureTransportReasonWithTheDefaultSuffixes:
    def test_an_in_cluster_svc_cluster_local_base_url_is_allowed(self) -> None:
        reason = insecure_transport_reason(
            "http://niuu-volundr.volundr.svc.cluster.local",
            allow_plaintext=False,
        )
        assert reason is None

    def test_an_in_cluster_ravn_base_url_is_allowed_the_same_way(self) -> None:
        """The exemption applies to every dial_url configured_dial_urls()
        would hand back — ravn_base_url included — not only base_url."""
        reason = insecure_transport_reason(
            "http://niuu-ravn.volundr.svc.cluster.local",
            allow_plaintext=False,
        )
        assert reason is None

    def test_the_short_svc_form_is_also_allowed(self) -> None:
        reason = insecure_transport_reason(
            "http://niuu-observatory.volundr.svc",
            allow_plaintext=False,
        )
        assert reason is None

    def test_an_unrelated_public_hostname_is_still_refused(self) -> None:
        reason = insecure_transport_reason(
            "http://volundr.example.com",
            allow_plaintext=False,
        )
        assert reason is not None
        assert "https://" in reason

    def test_a_url_with_no_hostname_is_still_refused_not_crashed_on(self) -> None:
        """A malformed dial_url (no host at all) must fall through to the
        ordinary refusal, never raise out of the suffix check itself."""
        reason = insecure_transport_reason("http://", allow_plaintext=False)
        assert reason is not None


class TestInsecureTransportReasonWithAnEmptySuffixList:
    def test_the_ymir_style_seed_is_refused_once_the_list_is_emptied(self) -> None:
        configure_trusted_plaintext_host_suffixes([])
        reason = insecure_transport_reason(
            "http://niuu-volundr.volundr.svc.cluster.local",
            allow_plaintext=False,
        )
        assert reason is not None
        assert "https://" in reason

    def test_localhost_remains_exempt_regardless_of_the_suffix_list(self) -> None:
        """Emptying the suffix list only removes the *suffix* exemption —
        LOCAL_HOSTNAMES is a wholly separate, unconditional exemption."""
        configure_trusted_plaintext_host_suffixes([])
        assert insecure_transport_reason("http://localhost:8080", allow_plaintext=False) is None

    def test_explicit_allow_plaintext_still_works_with_an_empty_list(self) -> None:
        configure_trusted_plaintext_host_suffixes([])
        reason = insecure_transport_reason(
            "http://niuu-volundr.volundr.svc.cluster.local",
            allow_plaintext=True,
        )
        assert reason is None


class TestConfigureAndDefault:
    def test_default_trusted_plaintext_host_suffixes_reflects_the_current_config(self) -> None:
        configure_trusted_plaintext_host_suffixes([".internal"])
        assert default_trusted_plaintext_host_suffixes() == (".internal",)

    def test_configure_accepts_any_iterable_not_only_a_list(self) -> None:
        configure_trusted_plaintext_host_suffixes(s for s in (".a", ".b"))
        assert default_trusted_plaintext_host_suffixes() == (".a", ".b")

    def test_the_module_default_out_of_the_box_is_svc_cluster_local_and_svc(self) -> None:
        assert transport_security._DEFAULT_TRUSTED_PLAINTEXT_HOST_SUFFIXES == (
            ".svc.cluster.local",
            ".svc",
        )
