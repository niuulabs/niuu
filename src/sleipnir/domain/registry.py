"""Sleipnir event type constants.

All event types are defined here as module-level constants to avoid magic
strings throughout the codebase. Import the constant, not the string literal.

Organised by namespace:

- ``RAVN_*`` — Ravn agent events
- ``TYR_*``  — Tyr autonomous dispatcher events
- ``VOLUNDR_*`` — Volundr platform events
- ``BIFROST_*`` — Bifrost gateway events
- ``SYSTEM_*`` — Infrastructure and lifecycle events
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# ravn — Ravn agent events
# ---------------------------------------------------------------------------

#: A tool call was dispatched to an external executor.
RAVN_TOOL_CALL: str = "ravn.tool.call"

#: A tool call completed successfully.
RAVN_TOOL_COMPLETE: str = "ravn.tool.complete"

#: A tool call failed with an error.
RAVN_TOOL_ERROR: str = "ravn.tool.error"

#: A reasoning step started within an agent loop.
RAVN_STEP_START: str = "ravn.step.start"

#: A reasoning step completed within an agent loop.
RAVN_STEP_COMPLETE: str = "ravn.step.complete"

#: An agent session started.
RAVN_SESSION_START: str = "ravn.session.start"

#: An agent session ended (gracefully or via interrupt).
RAVN_SESSION_END: str = "ravn.session.end"

#: An agent produced a final response.
RAVN_RESPONSE_COMPLETE: str = "ravn.response.complete"

#: An interrupt signal was received by the agent.
RAVN_INTERRUPT: str = "ravn.interrupt"

#: A Ravn task turn started (agent received a new user request).
RAVN_TURN_START: str = "ravn.turn.start"

#: A tool invocation started (dispatched to executor, awaiting result).
RAVN_TOOL_START: str = "ravn.tool.start"

#: A Ravn task completed successfully (all turns and tool calls done).
#: @deprecated Use RAVN_TASK_COMPLETED ("ravn.task.completed") instead.
RAVN_TASK_COMPLETE: str = "ravn.task.complete"

#: The agent requires a human decision before proceeding (urgency ≥ 0.8).
RAVN_DECISION_REQUIRED: str = "ravn.decision.required"

#: An agent session started (first turn received by the agent).
RAVN_SESSION_STARTED: str = "ravn.session.started"

#: An agent session ended — normal exit, interrupt, or error.
RAVN_SESSION_ENDED: str = "ravn.session.ended"

#: A Ravn task completed (all turns done; includes outcome and token cost).
RAVN_TASK_COMPLETED: str = "ravn.task.completed"

# ---------------------------------------------------------------------------
# tyr — Tyr autonomous dispatcher events
# ---------------------------------------------------------------------------

#: A new task was queued in the dispatcher.
TYR_TASK_QUEUED: str = "tyr.task.queued"

#: The dispatcher started executing a task.
TYR_TASK_STARTED: str = "tyr.task.started"

#: A task completed successfully.
TYR_TASK_COMPLETE: str = "tyr.task.complete"

#: A task failed.
TYR_TASK_FAILED: str = "tyr.task.failed"

#: A task was cancelled before completion.
TYR_TASK_CANCELLED: str = "tyr.task.cancelled"

#: A Saga (long-running multi-step task) was created.
TYR_SAGA_CREATED: str = "tyr.saga.created"

#: A Saga advanced to the next step.
TYR_SAGA_STEP: str = "tyr.saga.step"

#: A Saga completed all steps.
TYR_SAGA_COMPLETE: str = "tyr.saga.complete"

#: A Saga failed and was rolled back.
TYR_SAGA_FAILED: str = "tyr.saga.failed"

#: A dispatcher session started.
TYR_SESSION_START: str = "tyr.session.start"

#: A dispatcher session ended.
TYR_SESSION_END: str = "tyr.session.end"

#: A raid was created.
TYR_RAID_CREATED: str = "tyr.raid.created"

#: A raid changed state (submitted, running, waiting_review, done, failed, etc.).
TYR_RAID_STATE_CHANGED: str = "tyr.raid.state_changed"

#: A raid completed successfully.
TYR_RAID_COMPLETED: str = "tyr.raid.completed"

#: A raid failed.
TYR_RAID_FAILED: str = "tyr.raid.failed"

#: The autonomous dispatcher state was updated.
TYR_DISPATCHER_STATE: str = "tyr.dispatcher.state"

#: A notification was sent to a channel.
TYR_NOTIFICATION_SENT: str = "tyr.notification.sent"

#: A Saga completed all phases (distinct from TYR_SAGA_COMPLETE for consistency with catalog).
TYR_SAGA_COMPLETED: str = "tyr.saga.completed"

#: A raid requires human approval before it can proceed.
TYR_RAID_NEEDS_APPROVAL: str = "tyr.raid.needs_approval"

# ---------------------------------------------------------------------------
# skuld — Skuld broker events (session pod activity)
# ---------------------------------------------------------------------------

#: A Skuld broker session started.
SKULD_SESSION_STARTED: str = "skuld.session.started"

#: A Skuld broker session stopped.
SKULD_SESSION_STOPPED: str = "skuld.session.stopped"

#: A tool was executed in the Skuld session.
SKULD_TOOL_USE: str = "skuld.tool.use"

#: Token counts were updated in the Skuld session.
SKULD_TOKEN_UPDATE: str = "skuld.token.update"

#: A permission request was raised by the agent.
SKULD_PERMISSION_REQUEST: str = "skuld.permission.request"

# ---------------------------------------------------------------------------
# volundr — Volundr platform events
# ---------------------------------------------------------------------------

#: A session was created in Volundr.
VOLUNDR_SESSION_CREATED: str = "volundr.session.created"

#: A session was started (pods running, ready to accept CLI connections).
VOLUNDR_SESSION_STARTED: str = "volundr.session.started"

#: A session was stopped gracefully.
VOLUNDR_SESSION_STOPPED: str = "volundr.session.stopped"

#: A session failed (pod error or infrastructure failure).
VOLUNDR_SESSION_FAILED: str = "volundr.session.failed"

#: Token usage was recorded for a session.
VOLUNDR_TOKEN_USAGE: str = "volundr.token.usage"

#: A chronicle was created for a session.
VOLUNDR_CHRONICLE_CREATED: str = "volundr.chronicle.created"

#: An existing chronicle was updated.
VOLUNDR_CHRONICLE_UPDATED: str = "volundr.chronicle.updated"

#: A pull request was opened in the platform.
VOLUNDR_PR_OPENED: str = "volundr.pr.opened"

#: A pull request was closed (merged or abandoned).
VOLUNDR_PR_CLOSED: str = "volundr.pr.closed"

#: A pull request received a review comment.
VOLUNDR_PR_REVIEWED: str = "volundr.pr.reviewed"

#: A repository integration was registered.
VOLUNDR_REPO_REGISTERED: str = "volundr.repo.registered"

#: A repository integration was removed.
VOLUNDR_REPO_REMOVED: str = "volundr.repo.removed"

#: A CI pipeline run started.
VOLUNDR_PIPELINE_STARTED: str = "volundr.pipeline.started"

#: A CI pipeline run completed.
VOLUNDR_PIPELINE_COMPLETE: str = "volundr.pipeline.complete"

#: A CI pipeline run failed.
VOLUNDR_PIPELINE_FAILED: str = "volundr.pipeline.failed"

#: A session was updated (status change, token usage, etc.).
VOLUNDR_SESSION_UPDATED: str = "volundr.session.updated"

#: A session was deleted.
VOLUNDR_SESSION_DELETED: str = "volundr.session.deleted"

#: A session encountered an error.
VOLUNDR_SESSION_ERROR: str = "volundr.session.error"

#: Aggregate stats were updated.
VOLUNDR_STATS_UPDATED: str = "volundr.stats.updated"

#: A chronicle record was deleted.
VOLUNDR_CHRONICLE_DELETED: str = "volundr.chronicle.deleted"

#: A user message was recorded in a session.
VOLUNDR_MESSAGE_USER: str = "volundr.message.user"

#: An assistant message was recorded in a session.
VOLUNDR_MESSAGE_ASSISTANT: str = "volundr.message.assistant"

#: A file was created in the session workspace.
VOLUNDR_FILE_CREATED: str = "volundr.file.created"

#: A file was modified in the session workspace.
VOLUNDR_FILE_MODIFIED: str = "volundr.file.modified"

#: A file was deleted from the session workspace.
VOLUNDR_FILE_DELETED: str = "volundr.file.deleted"

#: A git commit was made in the session workspace.
VOLUNDR_GIT_COMMIT: str = "volundr.git.commit"

#: A git push was made from the session workspace.
VOLUNDR_GIT_PUSH: str = "volundr.git.push"

#: A git branch was created in the session workspace.
VOLUNDR_GIT_BRANCH: str = "volundr.git.branch"

#: A git checkout was performed in the session workspace.
VOLUNDR_GIT_CHECKOUT: str = "volundr.git.checkout"

#: A terminal command was executed in the session workspace.
VOLUNDR_TERMINAL_COMMAND: str = "volundr.terminal.command"

#: A tool was used in the session workspace.
VOLUNDR_TOOL_USE: str = "volundr.tool.use"

# ---------------------------------------------------------------------------
# bifrost — Bifrost gateway events
# ---------------------------------------------------------------------------

#: A client connection was established through Bifrost.
BIFROST_CONNECTION_OPEN: str = "bifrost.connection.open"

#: A client connection was closed.
BIFROST_CONNECTION_CLOSE: str = "bifrost.connection.close"

#: A routing decision was made by the gateway.
BIFROST_ROUTE_SELECTED: str = "bifrost.route.selected"

#: An authentication check succeeded.
BIFROST_AUTH_SUCCESS: str = "bifrost.auth.success"

#: An authentication check failed.
BIFROST_AUTH_FAILURE: str = "bifrost.auth.failure"

#: A rate limit was applied to a request.
BIFROST_RATE_LIMITED: str = "bifrost.rate.limited"

#: An LLM call completed — emitted after every successful request.
BIFROST_REQUEST_COMPLETE: str = "bifrost.request.complete"

#: An agent reached 80 %+ of its token budget — early warning.
BIFROST_QUOTA_WARNING: str = "bifrost.quota.warning"

#: An agent hit its hard token limit — calls will be rejected.
BIFROST_QUOTA_EXCEEDED: str = "bifrost.quota.exceeded"

#: The upstream LLM provider started returning errors.
BIFROST_PROVIDER_DOWN: str = "bifrost.provider.down"

#: The upstream LLM provider recovered and is healthy again.
BIFROST_PROVIDER_RECOVERED: str = "bifrost.provider.recovered"

#: A tenant's daily spend crossed 80 % of its configured cap; model downgraded.
BIFROST_BUDGET_DEGRADED: str = "bifrost.budget.degraded"

# ---------------------------------------------------------------------------
# system — Infrastructure and lifecycle events
# ---------------------------------------------------------------------------

#: A health check ping was emitted.
SYSTEM_HEALTH_PING: str = "system.health.ping"

#: A service started and is ready.
SYSTEM_SERVICE_STARTED: str = "system.service.started"

#: A service is shutting down.
SYSTEM_SERVICE_STOPPING: str = "system.service.stopping"

#: Configuration was reloaded without restart.
SYSTEM_CONFIG_RELOADED: str = "system.config.reloaded"

#: A recoverable error occurred at the infrastructure layer.
SYSTEM_ERROR: str = "system.error"

#: A metric snapshot was emitted for observability.
SYSTEM_METRIC: str = "system.metric"

# ---------------------------------------------------------------------------
# mimir — Mimir knowledge-base events
# ---------------------------------------------------------------------------

#: A wiki page was written (created or updated) by the Mimir adapter.
MIMIR_PAGE_WRITTEN: str = "mimir.page.written"

#: A dream cycle completed — pages updated, entities created, lint fixes applied.
MIMIR_DREAM_COMPLETED: str = "mimir.dream.completed"

# ---------------------------------------------------------------------------
# github — GitHub webhook events
# ---------------------------------------------------------------------------

#: A pull request was opened on GitHub.
GITHUB_PR_OPENED: str = "github.pr.opened"

#: A pull request was merged on GitHub.
GITHUB_PR_MERGED: str = "github.pr.merged"

#: A push was made to the main branch on GitHub.
GITHUB_PUSH_MAIN: str = "github.push.main"

#: An issue was opened on GitHub.
GITHUB_ISSUE_OPENED: str = "github.issue.opened"
