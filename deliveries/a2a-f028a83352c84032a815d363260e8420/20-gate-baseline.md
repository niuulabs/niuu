# NIU-295 clean-regin gate baseline

## Verdict

The full repository gate fails independently of the NIU-295 branch.  A clean,
detached worktree at `origin/regin` commit `a7d6c5ad204cf96e260bccc3c2737f6cfd8d5f0e`
(the base branch with no NIU-295 branch commits) produced **266 failures**.
NIU-295 changes no production code and no persona, workload-identity, chart,
SSH, Docker, or tool-verification tests. This establishes the gate failures as
pre-existing on the clean base in this environment; they are not fixed,
suppressed, skipped, or threshold-adjusted by this delivery.

## Command and result

The clean worktree initially lacked the development test extra, so the
`--extra k8s` invocation stopped at collection with missing `nats` and
`textual` modules. With the repository's development and Kubernetes test
extras installed, the complete gate was run without selecting, deselecting,
or skipping any additional tests:

```text
uv run --extra dev --extra k8s pytest tests/ -v --tb=short --cov=src --cov-report=term-missing --cov-fail-under=85
= 266 failed, 26439 passed, 187 skipped, 230 deselected, 1 xfailed, 114 warnings in 1508.95s (0:25:08) =
```

## Failure categories

- **182 missing Helm executable:** `FileNotFoundError: [Errno 2] No such file or directory: 'helm'`.
- **79 workload-token exchange failures:** 63 summary lines explicitly show the unmocked or 404 `POST .../api/v1/tokens/workload/exchange`; 16 persona-adapter assertions are downstream of that same exchange failure (their captured logs show the 404 rather than repeating it in the one-line summary).
- **5 other pre-existing environment/test failures:** one SSH lock timing assertion, two Docker-not-found CLI tests, one persisted-commission assertion, and one static-verification assertion.

## Verbatim pytest failure summaries

The following are the exact `FAILED ...` summary lines emitted by the clean-regin gate:

```text
FAILED tests/integration/test_http_persona_adapter.py::test_round_trip_load - assert None is not None
FAILED tests/integration/test_http_persona_adapter.py::test_round_trip_list_names - AssertionError: assert 'alpha-agent' in []
FAILED tests/test_adapters/test_ssh_host_preparation.py::test_supervisor_releases_lock_at_deadline_after_controller_death - Failed: DID NOT RAISE ProcessLookupError
FAILED tests/test_charts/test_access_log_redaction.py::test_envoy_access_log_never_includes_the_query_string[bifrost] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_access_log_redaction.py::test_envoy_access_log_never_includes_the_query_string[guild] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_access_log_redaction.py::test_envoy_access_log_never_includes_the_query_string[mimir] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_access_log_redaction.py::test_envoy_access_log_never_includes_the_query_string[niuu-shared] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_access_log_redaction.py::test_envoy_access_log_never_includes_the_query_string[observatory] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_access_log_redaction.py::test_envoy_access_log_never_includes_the_query_string[ravn] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_access_log_redaction.py::test_envoy_access_log_never_includes_the_query_string[skuld] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_access_log_redaction.py::test_envoy_access_log_never_includes_the_query_string[skuld-planner] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_access_log_redaction.py::test_envoy_access_log_never_includes_the_query_string[ting] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_access_log_redaction.py::test_envoy_access_log_never_includes_the_query_string[volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_access_log_redaction.py::test_nginx_access_log_never_includes_the_query_string[skuld] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_access_log_redaction.py::test_nginx_access_log_never_includes_the_query_string[skuld-planner] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_auth_migrations.py::test_auth_migration_copies_match[volundr-names0-volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_auth_migrations.py::test_auth_migration_copies_match[guild-names1-volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_auth_migrations.py::test_auth_migration_copies_match[niuu-shared-names2-volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_auth_migrations.py::test_auth_migration_copies_match[ting-names3-ting] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_gateway_filter_order_and_fail_closed - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_private_listeners_and_network_policy - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_unsafe_gateway_configuration_rejected[overrides0] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_unsafe_gateway_configuration_rejected[overrides1] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_unsafe_gateway_configuration_rejected[overrides2] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_unsafe_gateway_configuration_rejected[overrides3] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_unsafe_gateway_configuration_rejected[overrides4] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_hardened_pat_revocation_settings_reach_application - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_shared_gateway_listener_and_resource_authorization - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_remote_workload_keys_require_verified_tls[bad0-volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_remote_workload_keys_require_verified_tls[bad0-niuu-shared] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_remote_workload_keys_require_verified_tls[bad1-volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_remote_workload_keys_require_verified_tls[bad1-niuu-shared] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_explicit_no_auth_profile[volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_explicit_no_auth_profile[niuu-shared] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_explicit_no_auth_profile[ting] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_explicit_no_auth_profile[skuld] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ting_scope_is_bound_to_launch_route[/api/v1/ting/workflows/abc/launch-True] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ting_scope_is_bound_to_launch_route[/api/v1/ting/workflows/abc/delete-False] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ting_scope_is_bound_to_launch_route[/api/v1/ting/workflows/abc/nested/launch-False] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ting_scope_is_bound_to_launch_route[/api/v1/ting/workflows//launch-False] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ting_gateway_admits_its_a2a_card_and_endpoint[GET-/.well-known/agent-card.json-True] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ting_gateway_admits_its_a2a_card_and_endpoint[HEAD-/.well-known/agent-card.json-True] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ting_gateway_admits_its_a2a_card_and_endpoint[POST-/.well-known/agent-card.json-False] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ting_gateway_admits_its_a2a_card_and_endpoint[POST-/api/v1/ting/a2a-True] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ravn_explicit_no_auth_profile - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_skuld_secure_profile_has_private_backends_and_verified_jwks - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_central_identity_wires_edge_application_and_pat_revocation[volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_central_identity_wires_edge_application_and_pat_revocation[ting] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_central_identity_wires_edge_application_and_pat_revocation[skuld] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ext_authz_deadline_covers_the_identity_lookup[overrides0-1s-volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ext_authz_deadline_covers_the_identity_lookup[overrides0-1s-ting] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ext_authz_deadline_covers_the_identity_lookup[overrides0-1s-skuld] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ext_authz_deadline_covers_the_identity_lookup[overrides1-6s-volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ext_authz_deadline_covers_the_identity_lookup[overrides1-6s-ting] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ext_authz_deadline_covers_the_identity_lookup[overrides1-6s-skuld] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ext_authz_deadline_covers_the_identity_lookup[overrides2-3s-volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ext_authz_deadline_covers_the_identity_lookup[overrides2-3s-ting] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ext_authz_deadline_covers_the_identity_lookup[overrides2-3s-skuld] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ext_authz_timeout_rejects_unparseable_duration[volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ext_authz_timeout_rejects_unparseable_duration[ting] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_ext_authz_timeout_rejects_unparseable_duration[skuld] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_no_auth_overrides_central_identity_profile[volundr] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_no_auth_overrides_central_identity_profile[ting] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_no_auth_overrides_central_identity_profile[skuld] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_no_auth_overrides_central_identity_profile[guild] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_no_auth_overrides_central_identity_profile[observatory] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_standalone_gateway_central_identity_configuration[guild] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_cedar_authz.py::test_standalone_gateway_central_identity_configuration[observatory] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_persistent_server_and_secret_reference - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_postgres_maintenance_has_no_access_to_live_pglite_files - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_invalid_configuration_fails[args0-Scheduled dreams require engine=postgres] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_invalid_configuration_fails[args1-postgres.enabled requires engine=postgres] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_invalid_configuration_fails[args2-engine must be pglite or postgres] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_invalid_configuration_fails[args3-embedding.model is required] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_native_dream_jobs_are_separate_from_service - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_gbrain_rejects_warden - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_managed_postgres_uses_operator_credentials_and_configured_storage - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_discovery_identity_keeps_instances_distinct_and_selectors_stable[mimir] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_discovery_identity_keeps_instances_distinct_and_selectors_stable[gbrain] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_managed_database_discovery_relationship - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_database_admin_bootstrap_is_isolated_from_application_credentials - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_gbrain_chart.py::test_connection_bootstrap_uses_native_scoped_token_and_one_secret - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_mimir_embedded_warden.py::test_chart_binds_custom_storage_and_manual_warden_profile - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_niuu_shared_chart.py::test_credential_enrollment_runner_renders_unsupported_by_default - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_niuu_shared_chart.py::test_native_login_runner_has_dedicated_namespace_and_scoped_rbac - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_niuu_shared_chart.py::test_credential_enrollment_runner_secret_kwargs_reach_the_deployment - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_persistent_scratch.py::test_chart_isolates_temp_and_reuses_user_cache - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_persistent_scratch.py::test_chart_rejects_unsafe_scratch_identity[] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_persistent_scratch.py::test_chart_rejects_unsafe_scratch_identity[../other] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_persistent_scratch.py::test_chart_rejects_unsafe_scratch_identity[x";touch /tmp/bad;#] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_persistent_scratch.py::test_chart_rejects_conflicting_tmp_mount - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestNoStoreConfiguredByDefault::test_renders_successfully - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestNoStoreConfiguredByDefault::test_no_configmap_rendered - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestNoStoreConfiguredByDefault::test_no_config_volume_mounted - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestNoStoreConfiguredByDefault::test_no_dsn_env_var - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestNoStoreConfiguredByDefault::test_no_pvc_rendered - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestNoStoreConfiguredByDefault::test_renders_successfully_with_multiple_replicas - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestNoStoreConfiguredByDefault::test_renders_successfully_with_autoscaling - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceAloneWithoutTriggerStoreOrBudgetLedgerOptIn::test_no_configmap_rendered - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceAloneWithoutTriggerStoreOrBudgetLedgerOptIn::test_no_dsn_env_var - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceAloneWithoutTriggerStoreOrBudgetLedgerOptIn::test_renders_successfully_with_multiple_replicas - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestDatabaseAloneWithoutTriggerStoreOrBudgetLedgerOptIn::test_renders_without_an_existing_secret - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestDatabaseAloneWithoutTriggerStoreOrBudgetLedgerOptIn::test_no_configmap_rendered - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestDatabaseAloneWithoutTriggerStoreOrBudgetLedgerOptIn::test_no_dsn_env_var - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestTriggerStoreAndBudgetLedgerOptIn::test_file_store_via_persistence - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestTriggerStoreAndBudgetLedgerOptIn::test_file_paths_are_under_the_persistence_mount - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestTriggerStoreAndBudgetLedgerOptIn::test_custom_mount_path_is_honoured - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestTriggerStoreAndBudgetLedgerOptIn::test_config_volume_is_mounted - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestTriggerStoreAndBudgetLedgerOptIn::test_operator_settings_are_preserved_alongside_the_synthesized_stores - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestTriggerStoreAndBudgetLedgerOptIn::test_only_trigger_store_opted_in_leaves_budget_ledger_unset - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestTriggerStoreAndBudgetLedgerOptIn::test_postgres_store_via_database - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestTriggerStoreAndBudgetLedgerOptIn::test_database_takes_precedence_over_persistence_when_both_set - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestTriggerStoreAndBudgetLedgerOptIn::test_dsn_env_var_sourced_from_the_existing_secret - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestTriggerStoreAndBudgetLedgerOptIn::test_trigger_store_and_budget_ledger_share_one_dsn_env_var - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestTriggerStoreAndBudgetLedgerOptIn::test_custom_secret_key_is_honoured - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestTriggerStoreAndBudgetLedgerOptIn::test_config_volume_and_env_are_rendered_for_postgres - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceValidation::test_default_values_render_successfully - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceValidation::test_trigger_store_enabled_without_persistence_or_database_fails - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceValidation::test_budget_ledger_enabled_without_persistence_or_database_fails - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceValidation::test_trigger_store_with_persistence_at_one_replica_renders - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceValidation::test_trigger_store_with_database_renders - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceValidation::test_trigger_store_with_persistence_and_replica_count_above_one_fails - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceValidation::test_trigger_store_with_persistence_and_autoscaling_fails - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceValidation::test_trigger_store_with_database_and_autoscaling_renders - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceValidation::test_trigger_store_with_database_and_replica_count_above_one_renders - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceValidation::test_a_store_enabled_without_persistence_or_database_fails - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceValidation::test_a_postgres_adapter_without_database_enabled_fails - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_ravn_chart.py::TestPersistenceValidation::test_missing_existing_secret_fails_the_render - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_skuld_chart.py::test_websocket_auth_configuration_reaches_broker[False] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_skuld_chart.py::test_websocket_auth_configuration_reaches_broker[True] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestRbacTemplate::test_cluster_role_never_grants_secrets_for_execution_credentials[true] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestRbacTemplate::test_cluster_role_never_grants_secrets_for_execution_credentials[false] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestRbacTemplate::test_namespaced_role_grants_secret_writes_for_execution_credentials - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestRbacTemplate::test_namespaced_role_is_read_only_on_secrets_without_execution_credentials - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_empty_resident_profiles_render_as_list - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_resident_a2a_settings_render_into_config - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_notifications_section_renders_into_config - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_push_renders_with_the_sink_the_needs_input_push_needs - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_forge_mcp_section_renders_into_config - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_observability_is_absent_by_default - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_observability_block_renders_from_values - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_execution_credential_projection_defaults_to_release_namespace - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_execution_credential_projection_namespace_override_is_single_key - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_execution_credential_projection_namespace_is_not_duplicated - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_ci_values_run_resident_runtime_migrations[overrides0-ghcr.io/niuulabs/niuu:pr-993] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_ci_values_run_resident_runtime_migrations[overrides1-registry.example/niuu:release] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_resident_session_controllers_render_with_backend_binding - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_resident_session_controller_secret_kwargs_reach_the_container - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_ravn_sessions_grant_secret_writes_only_in_the_residents_namespace - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_flux_residents_can_always_read_their_pod_logs[controller0] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_flux_residents_can_always_read_their_pod_logs[controller1] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_no_resident_logs_role_without_a_flux_controller - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_openshell_residents_can_always_manage_their_workspace_claims - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_openshell_resident_controller_uses_its_own_workspace_namespace - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_no_workspace_role_when_openshell_residents_keep_workspaces_in_sandboxes - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_no_workspace_role_without_an_openshell_controller - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_no_resident_secret_role_without_ravn_sessions - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestConfigMapTemplate::test_default_ravn_sessions_grant_secret_writes_in_the_residents_namespace - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestRavnResidentSessions::test_on_by_default_without_any_master_secret - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestRavnResidentSessions::test_switched_off - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestPreviewCacheStorage::test_configmap_renders_preview_cache_dir_default - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestPreviewCacheStorage::test_configmap_renders_preview_cache_dir_override - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestPreviewCacheStorage::test_deployment_mounts_preview_cache_emptydir_at_configured_path - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestPreviewCacheStorage::test_deployment_mounts_scratch_emptydir_at_tmp - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestPreviewCacheStorage::test_deployment_preview_cache_mount_path_follows_override - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestPreviewCacheStorage::test_deployment_preview_cache_emptydir_has_no_size_limit_when_unset - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestResidentWorkloadIdentityMapping::test_disabled_by_default_renders_no_resident_mapping - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestResidentWorkloadIdentityMapping::test_enabled_renders_a_subject_prefix_scoped_mapping - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestResidentWorkloadIdentityMapping::test_enabled_without_namespace_fails_the_render - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestResidentWorkloadIdentityMapping::test_operator_supplied_mappings_are_preserved_alongside_the_resident_one - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestResidentWorkloadIdentityMapping::test_resident_mapping_is_tried_before_operator_mappings - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestResidentWorkloadIdentityMapping::test_owner_id_pattern_extracts_only_the_runtime_uuid - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestResidentWorkloadIdentityMapping::test_owner_id_pattern_does_not_match_a_non_uuid_resident_name - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestWorkloadIdentityTenantResolver::test_disabled_by_default - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestWorkloadIdentityTenantResolver::test_configured_adapter_and_secret_kwargs_render - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_charts/test_volundr_chart.py::TestWorkloadIdentityTenantResolver::test_secret_kwargs_env_var_is_sourced_from_the_named_secret - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_cli/test_stack_commands.py::TestStackUp::test_uses_native_lan_unless_external_host_is_configured[] - RuntimeError: docker not found in PATH; run `niuu doctor`.
FAILED tests/test_cli/test_stack_commands.py::TestStackUp::test_uses_native_lan_unless_external_host_is_configured[spark.local] - RuntimeError: docker not found in PATH; run `niuu doctor`.
FAILED tests/test_ravn/test_agent.py::TestRavnAgentToolUse::test_build_tool_reuses_persisted_commission_after_restart - AssertionError: assert 'ead6d07d-2b9b-4d67-9867-e6f1d1fe8d2d' == 'newest-repair-operation'
FAILED tests/test_ravn/test_personas/test_http.py::TestLoad::test_load_returns_persona_config - assert False
FAILED tests/test_ravn/test_personas/test_http.py::TestLoad::test_load_parses_llm_fields - assert None is not None
FAILED tests/test_ravn/test_personas/test_http.py::TestLoad::test_load_parses_consumes_and_fan_in - assert None is not None
FAILED tests/test_ravn/test_personas/test_http.py::TestLoad::test_load_parses_produces - assert None is not None
FAILED tests/test_ravn/test_personas/test_http.py::TestLoad::test_load_returns_stale_cache_on_500 - assert None is not None
FAILED tests/test_ravn/test_personas/test_http.py::TestLoad::test_load_returns_stale_cache_on_network_error - KeyError: 'coder'
FAILED tests/test_ravn/test_personas/test_http.py::TestLoad::test_load_logs_warning_on_5xx - assert False
FAILED tests/test_ravn/test_personas/test_http.py::TestPortableSource::test_load_current_portable_returns_raw_document - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://volundr.test/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_personas/test_http.py::TestPortableSource::test_load_exact_portable_revision_uses_revision_endpoint - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://volundr.test/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_personas/test_http.py::TestPortableSource::test_load_current_portable_does_not_use_stale_fallback_on_server_error - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://volundr.test/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_personas/test_http.py::TestCache::test_cache_hit_single_http_request - AssertionError: assert 0 == 1
FAILED tests/test_ravn/test_personas/test_http.py::TestCache::test_cache_expiry_triggers_new_request - KeyError: 'coder'
FAILED tests/test_ravn/test_personas/test_http.py::TestCache::test_list_names_cache_hit - AssertionError: assert 0 == 1
FAILED tests/test_ravn/test_personas/test_http.py::TestCache::test_list_names_cache_expiry - AttributeError: 'NoneType' object has no attribute 'expires_at' and no __dict__ for setting new attributes
FAILED tests/test_ravn/test_personas/test_http.py::TestListNames::test_list_names_returns_sorted_names - AssertionError: assert [] == ['coder', 'reviewer']
FAILED tests/test_ravn/test_personas/test_http.py::TestListNames::test_list_names_returns_stale_cache_on_500 - AttributeError: 'NoneType' object has no attribute 'expires_at' and no __dict__ for setting new attributes
FAILED tests/test_ravn/test_personas/test_http.py::TestListNames::test_list_names_returns_stale_cache_on_network_error - AttributeError: 'NoneType' object has no attribute 'expires_at' and no __dict__ for setting new attributes
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrSessionTool::test_list_sessions - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrSessionTool::test_create_session - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrSessionTool::test_get_session - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrSessionTool::test_create_session_missing_name - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrSessionTool::test_stop_session - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrSessionTool::test_start_session - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrSessionTool::test_stop_session_missing_id - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrSessionTool::test_delete_session - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrSessionTool::test_unknown_action - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrSessionTool::test_api_error_returns_error_result - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrGitTool::test_list_repos - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrGitTool::test_list_branches - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrGitTool::test_list_branches_missing_repo_url - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrGitTool::test_create_pr - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrGitTool::test_create_pr_missing_session_id - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrGitTool::test_ci_status - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrGitTool::test_ci_status_missing_fields - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestVolundrGitTool::test_unknown_action - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTingSagaTool::test_list_sagas - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingSagaTool::test_commit_saga - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingSagaTool::test_commit_saga_missing_fields - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTingSagaTool::test_dispatch_saga - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingSagaTool::test_dispatch_missing_items - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTingSagaTool::test_get_saga - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingSagaTool::test_list_runs - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingSagaTool::test_unknown_action - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTrackerIssueTool::test_search_issues - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTrackerIssueTool::test_search_issues_missing_query - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTrackerIssueTool::test_update_status - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTrackerIssueTool::test_update_status_missing_fields - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTrackerIssueTool::test_get_issue - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTrackerIssueTool::test_get_issue_missing_id - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTrackerIssueTool::test_unknown_action - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTingWorkflowTool::test_list_workflows - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingWorkflowTool::test_get_workflow - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingWorkflowTool::test_get_workflow_missing_id - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTingWorkflowTool::test_launch_workflow - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingWorkflowTool::test_launch_workflow_joins_when_chat_endpoint_returned - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingWorkflowTool::test_launch_workflow_reports_join_skipped_without_chat_endpoint - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingWorkflowTool::test_launch_workflow_by_configured_alias_id - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingWorkflowTool::test_launch_workflow_alias_preserves_explicit_context - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingWorkflowTool::test_launch_workflow_by_configured_alias_name - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingWorkflowTool::test_launch_workflow_unknown_alias - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTingWorkflowTool::test_launch_workflow_missing_fields - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTingWorkflowTool::test_unknown_action - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_launch_research_by_configured_alias_id - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_launch_research_defaults_to_research_alias - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_launch_research_preserves_explicit_question_context - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_launch_research_without_workflow_id_uses_platform_default - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_launch_echoes_slug_for_confirmation - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_failed_launch_says_no_campaign_exists - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_list_campaigns - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_get_campaign_returns_detail - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_get_missing_campaign_says_it_does_not_exist - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_get_requires_slug - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_artifacts_lists_campaign_output - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_artifacts_for_missing_campaign_is_an_error - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingResearchTool::test_unknown_action_is_rejected - httpx.HTTPStatusError: Client error '404 Not Found' for url 'http://localhost:8080/api/v1/tokens/workload/exchange'
FAILED tests/test_ravn/test_platform_tools.py::TestTingPlanTool::test_launch_plan_uses_configured_alias_defaults - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_platform_tools.py::TestTingSpecTool::test_launch_spec_uses_configured_alias_defaults - respx.models.AllMockedAssertionError: RESPX: <Request('POST', 'http://localhost:8080/api/v1/tokens/workload/exchange')> not mocked!
FAILED tests/test_ravn/test_tool_verification.py::TestStaticDefects::test_correct_code_is_clean - assert ["uses '__classdict__', which the tool never defines, imports or receives as an argument"] == []
FAILED tests/test_workload_identity.py::test_workload_jwt_provider_renders_as_additional_envoy_auth_provider[volundr-8080] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_workload_identity.py::test_workload_jwt_provider_renders_as_additional_envoy_auth_provider[ting-8081] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_workload_identity.py::test_workload_jwt_provider_renders_as_additional_envoy_auth_provider[guild-8084] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_workload_identity.py::test_workload_jwt_provider_renders_as_additional_envoy_auth_provider[niuu-shared-8082] - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_workload_identity.py::test_skuld_accepts_workload_jwt_at_gateway_and_sidecar - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_workload_identity.py::test_bifrost_accepts_workload_jwt_provider - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_workload_identity.py::test_ting_accepts_external_workload_jwks_provider - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_workload_identity.py::test_ravn_accepts_external_workload_jwks_provider - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
FAILED tests/test_workload_identity.py::test_observatory_accepts_forwarded_workload_identity - FileNotFoundError: [Errno 2] No such file or directory: 'helm'
```
