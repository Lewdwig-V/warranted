# Enforcement status of the invariants

[AGENTS.md](../../AGENTS.md#invariants-to-preserve) lists eight invariants and
says not to claim enforcement from documentation or a prompt alone. This page
records what actually enforces each clause today: the code, and the tests that
attempt a violation. A clause with no executable check is listed as such.

The tiered "enforcement ladder" is borrowed from Clauderizer's
[`docs/ENFORCEMENT.md`](https://github.com/CollinCusce/Clauderizer/blob/3b792597d2cfc05a72bcad82227eae007bbf41ca/docs/ENFORCEMENT.md)
(Collin Cusce and the Clauderizer contributors, Apache-2.0, read at version
2.0.3). That ladder names, for each discipline it asks of an agent, which
mechanism carries it, including "only the instructions text". Our adaptation
applies the idea to harness invariants rather than agent disciplines, and uses
tiers that distinguish tested negative cases from untested code.

Audited 2026-10-03 against `main` at `f588371`. Update a row in the same PR
that changes its enforcement, and cite the test that demonstrates it.

## Tiers

| Tier | Meaning |
| --- | --- |
| **Tested** | Code enforces the clause, and a test attempts a violation and shows it refused or detected. |
| **Partial** | Enforced on some surfaces or paths only. The row says which are not covered. |
| **Documented** | Stated in documentation only; no executable check. |
| **Not built** | The machinery the clause governs does not exist yet. |

Two assumptions underlie every row. The host process that writes the ledger is
trusted: `Ledger.record` accepts the `Origin` its caller supplies. And workers
never hold the ledger: they reach the host only through the model and tool
callbacks in `_worker.py`. File-system and credential isolation of the worker
rests on `Sandbox`, the default `Project` environment; those tests are marked
`container` and run in CI's containment job. A custom `environment=` is trusted
by its identity string.

Test names below are in `tests/`; `file::name` is a pytest node ID.

## 1. Raw evidence keeps its version and origin

| Clause | Tier | Enforced by | Negative cases |
| --- | --- | --- | --- |
| Raw evidence is versioned; changed or missing bytes are never repaired | Tested | `_ledger.py`: `Ledger.record`, digest checks on read, record-envelope versions | `test_ledger.py::test_records_remain_immutable_and_repeated_bytes_keep_their_origins`, `::test_artifact_damage_is_explicit_and_never_repaired`, `::test_unsupported_or_invalid_metadata_is_rejected` |
| Evidence retains its origin | Tested | `Ledger._check_origin` | `test_ledger.py::test_fresh_process_recovers_bytes_origins_and_sessions` |
| Agent-authored text cannot forge a tool result | Tested | Only the host parses operation requests (`_operations.py`); `Journal.call` in `_worker.py` | `test_host_operations.py::test_worker_output_cannot_forge_or_trigger_host_results`, `test_ledger.py::test_raw_names_and_success_text_do_not_select_paths_or_become_receipts`, `test_scoped_memory.py::test_a_note_cannot_claim_to_be_verified` |
| Agent-authored text cannot forge an acceptance receipt | Tested | `_acceptance.py`: only a committed completion of the exact host request counts | `test_acceptance.py::test_missing_unknown_forged_and_narrow_evidence_cannot_grant_acceptance`, `test_re_fixture.py::test_forged_verdicts_receipts_and_tiers_establish_nothing`, `test_proof_checks.py::test_a_forged_verdict_file_in_the_workspace_is_ignored` |

## 2. Validity is separate from applicability

| Clause | Tier | Enforced by | Negative cases |
| --- | --- | --- | --- |
| A stale premise does not turn a valid result false | Tested | `_claims.py`: `Claims.assess` returns validation and applicability separately | `test_claims.py::test_restart_propagates_staleness_without_rewriting_validation`, `test_proof_receipts.py::test_historical_claim_needs_no_live_tools_and_keeps_validation_when_stale` |
| Superseded premises block dependent applications | Partial | Task-layer memory withholds stale and unknown entries (`Project._snapshot`); acceptance treats a changed revision as stale. In the host kit, `Claims.assess` only *reports* `stale`, and callers must act on it. | `test_scoped_memory.py::test_a_changed_dependency_hides_only_the_facts_that_depend_on_it`, `::test_a_missing_dependency_is_unknown_and_withheld`, `test_acceptance.py::test_restart_rechecks_current_versions_before_reusing_an_acceptance` |

## 3. Gates have no opt-out; unknowns never pass

| Clause | Tier | Enforced by | Negative cases |
| --- | --- | --- | --- |
| Rules have recorded exceptions | Partial | Host kit: `Acceptance.record_exception` checks scope, owner, and reason. The task layer has no rules: every required check is a gate. | `test_acceptance.py::test_rule_exception_is_scoped_and_never_waives_a_gate` |
| Gates have no opt-out | Tested | `record_exception` refuses a gate waiver | Same test |
| Gate evidence is independently checked | Tested | `Acceptance._check` requires the exact target among the request's inputs; `Project._run_check` | `test_acceptance.py::test_changed_checker_or_environment_cannot_reuse_a_passing_receipt`, `::test_malformed_checker_result_never_counts_as_passed` |
| Unknown kinds and applicability never default to success | Tested | `Acceptance._current` | `test_acceptance.py::test_unknown_kind_and_empty_policy_fail_before_acceptance`, `::test_unknown_applicability_does_not_skip_a_gate`, `test_experimental_tasks.py::test_checker_outcomes_stay_distinct` |
| Missing or stale evidence never defaults to success | Tested | `_acceptance.py` | `test_acceptance.py::test_missing_unknown_forged_and_narrow_evidence_cannot_grant_acceptance`, `::test_restart_rechecks_current_versions_before_reusing_an_acceptance` |

## 4. The host checks gates at the protected transition

| Clause | Tier | Enforced by | Negative cases |
| --- | --- | --- | --- |
| Exact target, inputs, and environment | Tested | `Acceptance._check`; ledger lookups require equal requests and project context | `test_acceptance.py::test_changed_checker_or_environment_cannot_reuse_a_passing_receipt`, `test_experimental_tasks.py::test_changed_warranted_code_blocks_reopening_even_at_the_same_version`, `::test_editing_adding_or_removing_a_declared_source_blocks_reopening` |
| Current dependencies at the transition | Partial | Host kit: rechecked at every `accept`. Task layer: a run pins its contract; a project revision applies to new runs, and resuming an affected run is refused at `resume`. A run already in progress in another process continues under its pinned contract. | `test_acceptance.py::test_restart_rechecks_current_versions_before_reusing_an_acceptance`, `test_revisions.py::test_a_project_revision_applies_to_new_runs_only` |
| Workers cannot weaken the target, checker, or budget | Partial | Budgets are enforced by the host. Worker isolation is tested only under `container`. | `test_re_fixture.py::test_budgets_hold_against_a_worker_that_tries_to_exceed_them`, `test_experimental_tasks.py::test_check_budgets_require_isolated_checkers`, `test_revisions.py::test_a_revision_cannot_move_checks_outside_the_check_budget`, `test_sandbox.py::test_worker_cannot_read_host_state_or_credentials_and_capture_is_immutable` (`container`) |
| Optimisers cannot weaken them | Not built | No optimiser exists | — |

## 5. Usage and unknown outcomes survive restarts

| Clause | Tier | Enforced by | Negative cases |
| --- | --- | --- | --- |
| Cumulative usage survives restarts | Tested | Usage is derived from durable receipts in the ledger | `test_ledger.py::test_overrun_records_full_usage_and_blocks_new_or_pending_dispatch`, `test_scopes.py::test_scope_caps_survive_restart`, `test_attempts.py::test_cumulative_budget_blocks_next_request_after_billed_parse_failure` |
| Unresolved reservations survive restarts | Tested | Ledger reservations | `test_ledger.py::test_pending_and_unknown_requests_keep_one_reservation_across_sessions`, `::test_killed_host_recovers_reservation_or_atomic_completion`, `test_token_budgets.py::test_a_lost_response_keeps_its_whole_token_reservation_across_restart` |
| An unknown outcome is reconciled or left blocked | Tested | `Ledger.begin`, operation and receipt reconciliation | `test_host_operations.py::test_an_unknown_external_operation_without_a_receipt_stays_blocked`, `test_attempts.py::test_unprovable_or_invalid_receipt_never_defaults_to_free_retry`, `::test_receipt_from_different_operation_cannot_settle_unknown`, `test_worker.py::test_unknown_execution_blocks_resume_and_new_episode` |
| A side effect is never blindly retried | Tested | `begin` dispatches only a pending operation | `test_worker.py::test_crash_after_dispatch_marker_blocks_even_without_observed_effect`, `test_experimental_tasks.py::test_host_death_during_a_check_leaves_it_unknown_and_never_reruns_it`, `::test_host_death_mid_dispatch_leaves_the_run_unknown_without_retry`, `test_attempts.py::test_lost_response_reconciles_exact_receipt_without_second_post` |

Reconciling an operation that declares no external effect charges its full
reservation as an infrastructure failure. That relies on the domain's own
effect declaration.

## 6. Replay reveals only recorded outcomes

| Clause | Tier | Enforced by | Negative cases |
| --- | --- | --- | --- |
| Replay under compatible context, adding no evidence, calling no live tools | Not built | There is no replay engine ([M6](../roadmap.md#m6--dream-rsi-adaptation-for-search-improvement)) | — |
| Missing history is unsupported | Tested, for exported history | `_contexts.py`: `capture_history` refuses missing episodes | `test_m5_contexts.py::test_history_is_not_synthesized_for_a_missing_episode`, `::test_lost_attempt_cannot_be_exported_as_completed_history` |

Resume and reusable host operations reuse exact recorded completions. That is
not replay in the M6 sense.

## 7. Splits stay separate; failures and costs are reported

| Clause | Tier | Enforced by | Negative cases |
| --- | --- | --- | --- |
| Splits separated by provenance | Partial | For memory only: a memory scope cannot span splits, across campaigns | `test_campaigns.py::test_a_memory_scope_cannot_span_splits`, `::test_memory_is_shared_only_within_a_split_across_campaigns` |
| Splits separated by lineage | Documented | Nothing refuses the same task appearing in one split in one campaign and in another split in a later campaign | — |
| Failed attempts are reported | Partial | `campaign_report` and `RunStatus` include every planned run; the ledger is append-only. Only the unknown case is tested. | `test_campaigns.py::test_unknown_runs_are_resumed_when_the_campaign_runs_again` |
| Optimisation cost is reported | Not built | No optimiser exists | — |

## 8. A pass establishes only its stated scope

| Clause | Tier | Enforced by | Negative cases |
| --- | --- | --- | --- |
| A check establishes only its stated scope | Partial | Decisions bind the target, policy, revision, and check digests, and claims never grant acceptance. That a pass does not establish intent or whole-task success is documented, not executable. | `test_acceptance.py::test_missing_unknown_forged_and_narrow_evidence_cannot_grant_acceptance` |
| Specifications stay binding until an authorised revision | Partial | Task layer: `Project.revise`, with resume and pinned campaigns refused after a revision. "Authorised" means the host API caller; the revision's `owner` is not verified. | `test_revisions.py::test_a_project_revision_applies_to_new_runs_only`, `::test_a_campaign_pinned_before_a_revision_is_refused` |
| Failed independent obligations are preserved | Tested | Append-only decisions; gates are not waivable | `test_acceptance.py::test_restart_rechecks_current_versions_before_reusing_an_acceptance`, `::test_rule_exception_is_scoped_and_never_waives_a_gate` |
| Known gaps are preserved | Documented | Fixture and reference documentation | — |

## Known gaps found by this audit

- **A checker that returns something other than a `Verdict`** fails closed but
  is not recorded as an infrastructure failure. The error is raised after the
  check operation begins, outside the handler for checker faults, so the run is
  left `unknown` instead.
- **Lineage separation between splits** is not enforced (invariant 7).
- **Host-kit staleness** is reported, not enforced: a caller of `Claims` must
  refuse a stale application itself (invariant 2).
- **Rejected runs and cost totals** in campaign reports have no test.
