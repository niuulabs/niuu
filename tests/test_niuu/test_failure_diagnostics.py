from niuu.domain.failure_diagnostics import sanitize_failure_reason


def test_sanitize_failure_reason_redacts_credential_values() -> None:
    reason = sanitize_failure_reason(
        "Planning failed: Authorization: Bearer top-secret-token "
        "api_key=also-secret client_secret=client-secret-value "
        "client-secret=hyphen-client-secret "
        '{"client_secret": "json-client-secret"} '
        "{'client_secret': 'python-client-secret'}"
    )

    assert "top-secret-token" not in reason
    assert "also-secret" not in reason
    assert "client-secret-value" not in reason
    assert "hyphen-client-secret" not in reason
    assert "json-client-secret" not in reason
    assert "python-client-secret" not in reason
    assert "[REDACTED]" in reason
    assert "Authorization: Bearer [REDACTED]" in reason


def test_sanitize_failure_reason_preserves_complete_actionable_diagnostics() -> None:
    reason = "x" * 1_001

    assert sanitize_failure_reason(reason) == reason
