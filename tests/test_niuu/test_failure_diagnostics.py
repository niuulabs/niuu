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


def test_sanitize_failure_reason_redacts_escaped_quoted_credential_values() -> None:
    escaped_json_secret = r"safe\\\"json-leaked-tail"
    escaped_python_secret = r"safe\\\'python-leaked-tail"

    reason = sanitize_failure_reason(
        f'{{"client_secret": "{escaped_json_secret}"}} '
        f"{{'client_secret': '{escaped_python_secret}'}}"
    )

    assert escaped_json_secret not in reason
    assert escaped_python_secret not in reason
    assert "json-leaked-tail" not in reason
    assert "python-leaked-tail" not in reason
    assert reason == "{\"client_secret\":[REDACTED]} {'client_secret':[REDACTED]}"


def test_sanitize_failure_reason_redacts_unquoted_passphrases() -> None:
    passphrase = "correct horse battery staple"

    reason = sanitize_failure_reason(f"Repository access denied: password={passphrase}")

    for fragment in passphrase.split():
        assert fragment not in reason
    assert reason == "Repository access denied: password=[REDACTED]"
