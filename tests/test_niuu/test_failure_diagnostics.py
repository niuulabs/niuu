from niuu.domain.failure_diagnostics import sanitize_failure_reason


def test_sanitize_failure_reason_redacts_credential_values() -> None:
    reason = sanitize_failure_reason(
        "Planning failed: Authorization: Bearer top-secret-token api_key=also-secret"
    )

    assert "top-secret-token" not in reason
    assert "also-secret" not in reason
    assert "[REDACTED]" in reason
