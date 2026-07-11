"""Compatibility façade for the resident Valkyrie dashboard API."""

from __future__ import annotations

from ravn.api.valkyrie_event_projection import (
    _court_decision_entry,
    _court_decision_risk,
    _court_decision_status,
    _court_decisions_from_events,
    _event_dict,
    _event_environment_id,
    _event_kind,
    _event_log_entry,
    _event_tier,
    _event_timestamp,
    _event_valkyrie_id,
    _event_valkyrie_name,
    _is_raw_signal_event,
    _is_runtime_event,
    _operational_state_entry,
    _operational_states_from_events,
    _payload_float,
    _payload_int,
    _signal_entry,
    _signal_severity,
    _signal_subject,
    _signals_from_events,
    _state_drift,
    _structured_log_entry,
)  # noqa: F401
from ravn.api.valkyrie_inventory_projection import (
    _configured_environment_entries,
    _configured_flock_entries,
    _configured_huddle_entries,
    _configured_valkyrie_entries,
    _initial_dashboard,
    _signal_events,
)  # noqa: F401
from ravn.api.valkyrie_learning_projection import (
    _available_learning_scopes,
    _capability_from_signal_payload,
    _capability_gap_from_details,
    _dashboard_learning_from_telemetry,
    _decision_request_for_learning,
    _decision_summary,
    _learning_active_for_status,
    _learning_capability,
    _learning_edits,
    _learning_entry,
    _learning_feedback_action,
    _learning_status_for_event,
    _learning_status_rank,
    _merge_learning_entries,
    _merge_learning_record,
    _next_learning_scope,
    _previous_learning_scope,
    _raw_learning_id,
    _tool_need_entry,
)  # noqa: F401
from ravn.api.valkyrie_projection import (
    CONTROL_TELEMETRY_LIMIT,
    LEARNING_SCOPES,
    RAW_SIGNAL_TELEMETRY_LIMIT,
    Dashboard,
    ValkyrieDashboardProjection,
)  # noqa: F401
from ravn.api.valkyrie_projection_common import (
    _as_float,
    _as_int,
    _as_string_list,
    _canonical_environment_id,
    _empty_telemetry,
    _environment_id,
    _field,
    _first_transport_value,
    _live_report,
    _now,
    _rollup_health,
    _slug,
    _valkyrie_id,
)  # noqa: F401
from ravn.api.valkyrie_runtime_projection import (
    _huddle_role_for_action,
    _merge_observed_runtime,
    _merge_runtime_entry,
    _resolve_huddle_message_author,
    _runtime_entry,
    _runtime_event_key,
    _telemetry_activity,
    _validate_huddle_join_scope,
)  # noqa: F401
from ravn.api.valkyrie_telemetry_projection import (
    _aggregate_telemetry,
    _environment_telemetry_entry,
)  # noqa: F401
from ravn.api.valkyrie_requests import (
    LEARNING_FEEDBACK_VERDICTS,
    AutonomyUpdateRequest,
    HuddleJoinRequest,
    HuddleSendRequest,
    LearningDecisionRequest,
    LearningFeedbackRequest,
    LearningReviseRequest,
)  # noqa: F401
from ravn.api.valkyrie_routes import (
    OdinReviewCommandPublisher,
    ValkyrieRoomClient,
    _CommandTarget,
    _FanoutSleipnirPublisher,
    _review_item_for_learning_action,
    build_skuld_room_client_from_env,
    create_valkyrie_router,
)  # noqa: F401
from ravn.api.valkyrie_transport import (
    ValkyrieTelemetrySubscription,
    build_nats_review_command_publisher_from_env,
    build_nats_telemetry_subscription_from_env,
)  # noqa: F401
