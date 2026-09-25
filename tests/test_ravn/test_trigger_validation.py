"""Tests for ravn.api.trigger_validation."""

from __future__ import annotations

import pytest

from ravn.api.trigger_validation import (
    TriggerValidationError,
    check_repo_allowlisted,
    validate_trigger_spec,
)


class TestUnsupportedKind:
    def test_unsupported_kind_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="unsupported trigger kind"):
            validate_trigger_spec("webhook", "anything")

    def test_manual_kind_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="unsupported trigger kind"):
            validate_trigger_spec("manual", "anything")


class TestCronSpec:
    def test_empty_spec_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="must not be empty"):
            validate_trigger_spec("cron", "   ")

    def test_five_field_cron_expression_is_valid(self) -> None:
        validate_trigger_spec("cron", "*/15 * * * *")  # must not raise

    def test_natural_every_form_is_valid(self) -> None:
        validate_trigger_spec("cron", "every 30m")

    def test_bare_interval_form_is_valid(self) -> None:
        validate_trigger_spec("cron", "30m")

    def test_daily_at_form_is_valid(self) -> None:
        validate_trigger_spec("cron", "daily at 08:00")

    def test_iso_timestamp_form_is_valid(self) -> None:
        validate_trigger_spec("cron", "2026-04-07T08:00:00")

    def test_wrong_field_count_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="not a valid schedule"):
            validate_trigger_spec("cron", "not a cron expression")

    def test_non_numeric_field_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="invalid field"):
            validate_trigger_spec("cron", "abc * * * *")

    def test_day_of_week_name_range_raises(self) -> None:
        """Regression: '0 9 * * MON-FRI' must be rejected at creation time,
        not accepted and left to crash CronTrigger._is_due_canonical (via
        _field_matches's int() call) on every tick once persisted."""
        with pytest.raises(TriggerValidationError, match="invalid field"):
            validate_trigger_spec("cron", "0 9 * * MON-FRI")

    def test_hour_out_of_range_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="hour value '99' outside 0-23"):
            validate_trigger_spec("cron", "0 99 * * *")

    def test_minute_out_of_range_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="minute value '99' outside 0-59"):
            validate_trigger_spec("cron", "99 * * * *")

    def test_day_of_month_zero_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="day of month"):
            validate_trigger_spec("cron", "0 0 0 * *")

    def test_month_thirteen_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="month value '13' outside 1-12"):
            validate_trigger_spec("cron", "0 0 1 13 *")

    def test_day_of_week_seven_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="day of week value '7' outside 0-6"):
            validate_trigger_spec("cron", "0 0 * * 7")

    def test_range_form_is_range_checked(self) -> None:
        with pytest.raises(TriggerValidationError, match="hour value '25' outside 0-23"):
            validate_trigger_spec("cron", "0 20-25 * * *")

    def test_step_form_within_range_is_valid(self) -> None:
        validate_trigger_spec("cron", "0 */2 * * *")

    def test_comma_list_with_one_bad_value_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="minute value '61' outside 0-59"):
            validate_trigger_spec("cron", "1,15,61 * * * *")

    def test_zero_step_is_rejected_not_a_scheduler_crash(self) -> None:
        """Used to pass validation, then ZeroDivisionError inside the real
        scheduler's _field_matches the first time the cron loop ticked —
        permanently killing that resident's scheduler for every other
        trigger too. See ravn.adapters.triggers.cron._field_matches."""
        with pytest.raises(TriggerValidationError, match="non-positive step"):
            validate_trigger_spec("cron", "*/0 * * * *")

    def test_zero_step_on_a_range_is_also_rejected(self) -> None:
        with pytest.raises(TriggerValidationError, match="non-positive step"):
            validate_trigger_spec("cron", "1-5/0 * * * *")

    def test_range_with_step_is_valid_standard_cron_syntax(self) -> None:
        """Used to pass validation, then ValueError inside the real
        scheduler's _field_matches (int("1-5") — a parsing-order bug) the
        first time the cron loop ticked. Range-with-step is legitimate cron
        syntax (every `step`th minute within [lo, hi]), so the fix makes it
        actually work rather than rejecting valid syntax."""
        validate_trigger_spec("cron", "1-5/2 * * * *")  # must not raise

    def test_negative_step_is_rejected(self) -> None:
        with pytest.raises(TriggerValidationError, match="invalid field"):
            validate_trigger_spec("cron", "*/-1 * * * *")


class TestEventSpec:
    def test_empty_spec_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="must not be empty"):
            validate_trigger_spec("event", "   ", repo="org/repo")

    def test_unknown_event_type_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="not a known Sleipnir event type"):
            validate_trigger_spec("event", "github.pull_request.opened", repo="org/repo")

    def test_whitespace_containing_spec_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="not a known Sleipnir event type"):
            validate_trigger_spec("event", "github pull request opened", repo="org/repo")

    def test_known_event_type_without_repo_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="require a repo"):
            validate_trigger_spec("event", "github.pr.opened")

    def test_known_event_type_with_repo_is_valid(self) -> None:
        validate_trigger_spec("event", "github.pr.opened", repo="org/repo")


class TestCheckRepoAllowlisted:
    def test_ungranted_repo_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="is not granted repo"):
            check_repo_allowlisted(tenant_id="t1", repo="org/repo", grants={})

    def test_granted_repo_does_not_raise(self) -> None:
        check_repo_allowlisted(tenant_id="t1", repo="org/repo", grants={"t1": ["org/repo"]})

    def test_grant_for_a_different_tenant_does_not_apply(self) -> None:
        with pytest.raises(TriggerValidationError, match="is not granted repo"):
            check_repo_allowlisted(tenant_id="t2", repo="org/repo", grants={"t1": ["org/repo"]})

    def test_wildcard_grant_allows_any_repo(self) -> None:
        check_repo_allowlisted(tenant_id="t1", repo="anything/at-all", grants={"t1": ["*"]})

    def test_a_different_repo_for_the_same_tenant_still_raises(self) -> None:
        with pytest.raises(TriggerValidationError, match="is not granted repo"):
            check_repo_allowlisted(
                tenant_id="t1", repo="org/other-repo", grants={"t1": ["org/repo"]}
            )
