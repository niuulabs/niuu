-- Durable parent/child ledger for developer-delivery workflow expansion.

CREATE TABLE IF NOT EXISTS workflow_executions (
    id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    workflow_id UUID NOT NULL,
    workflow_revision TEXT NOT NULL,
    workflow_digest TEXT NOT NULL,
    repository TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    parent_session_id TEXT NOT NULL,
    parent_node_id TEXT NOT NULL,
    connection_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    current_generation INTEGER NOT NULL DEFAULT 0 CHECK (current_generation >= 0),
    total_budget BIGINT NOT NULL CHECK (total_budget > 0),
    reserved_budget BIGINT NOT NULL DEFAULT 0 CHECK (reserved_budget >= 0),
    spent_budget BIGINT NOT NULL DEFAULT 0 CHECK (spent_budget >= 0),
    deadline TIMESTAMPTZ NOT NULL,
    suspension_reason TEXT NOT NULL DEFAULT '',
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    policy JSONB NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
    launch_key TEXT NOT NULL DEFAULT '',
    launch_digest TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT workflow_executions_state_check CHECK (
        state IN ('pending', 'running', 'waiting', 'blocked', 'canceling',
                  'canceled', 'completed', 'failed')
    ),
    CONSTRAINT workflow_executions_budget_check CHECK (
        reserved_budget + spent_budget <= total_budget
    )
);

CREATE INDEX IF NOT EXISTS idx_workflow_executions_owner_updated
    ON workflow_executions (owner_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_active
    ON workflow_executions (state, deadline)
    WHERE state IN ('pending', 'running', 'waiting', 'blocked', 'canceling');
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_executions_launch_key
    ON workflow_executions (owner_id, tenant_id, launch_key)
    WHERE launch_key <> '';

CREATE TABLE IF NOT EXISTS workflow_execution_generations (
    execution_id UUID NOT NULL REFERENCES workflow_executions(id) ON DELETE CASCADE,
    generation INTEGER NOT NULL CHECK (generation > 0),
    sealed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sealed_at TIMESTAMPTZ,
    PRIMARY KEY (execution_id, generation)
);

CREATE TABLE IF NOT EXISTS workflow_execution_children (
    id UUID PRIMARY KEY,
    execution_id UUID NOT NULL REFERENCES workflow_executions(id) ON DELETE CASCADE,
    generation INTEGER NOT NULL CHECK (generation > 0),
    child_key TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    state TEXT NOT NULL,
    dependencies TEXT[] NOT NULL DEFAULT '{}',
    requirement_ids TEXT[] NOT NULL,
    objective TEXT NOT NULL,
    template_id UUID NOT NULL,
    template_revision TEXT NOT NULL,
    template_digest TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    input JSONB NOT NULL,
    repository TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    budget_units BIGINT NOT NULL CHECK (budget_units > 0),
    deadline TIMESTAMPTZ NOT NULL,
    agent_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    intent_id UUID NOT NULL UNIQUE,
    message_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL DEFAULT '',
    context_id TEXT NOT NULL DEFAULT '',
    result JSONB,
    artifacts JSONB NOT NULL DEFAULT '[]'::jsonb,
    workspace JSONB,
    evidence_report JSONB,
    evidence_validated_at TIMESTAMPTZ,
    failure_kind TEXT,
    error TEXT NOT NULL DEFAULT '',
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_token UUID,
    fencing_generation BIGINT NOT NULL DEFAULT 0 CHECK (fencing_generation >= 0),
    lease_expires_at TIMESTAMPTZ,
    remote_observed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (execution_id, generation, child_key, attempt),
    FOREIGN KEY (execution_id, generation)
        REFERENCES workflow_execution_generations(execution_id, generation) ON DELETE CASCADE,
    CONSTRAINT workflow_children_state_check CHECK (
        state IN ('reserved', 'launching', 'submitted', 'running', 'waiting',
                  'blocked', 'canceling', 'canceled', 'completed', 'failed', 'superseded')
    )
);

CREATE INDEX IF NOT EXISTS idx_workflow_children_execution
    ON workflow_execution_children (execution_id, generation, child_key, attempt);
CREATE INDEX IF NOT EXISTS idx_workflow_children_launch
    ON workflow_execution_children (state, lease_expires_at, deadline)
    WHERE state IN ('reserved', 'launching');
CREATE INDEX IF NOT EXISTS idx_workflow_children_task
    ON workflow_execution_children (agent_id, task_id)
    WHERE task_id <> '';

CREATE TABLE IF NOT EXISTS workflow_child_events (
    child_id UUID NOT NULL REFERENCES workflow_execution_children(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    state TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (child_id, event_id)
);

CREATE TABLE IF NOT EXISTS workflow_child_messages (
    id UUID PRIMARY KEY,
    child_id UUID NOT NULL REFERENCES workflow_execution_children(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL UNIQUE,
    answer TEXT NOT NULL,
    metadata JSONB NOT NULL,
    state TEXT NOT NULL DEFAULT 'reserved',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at TIMESTAMPTZ,
    CONSTRAINT workflow_child_messages_state_check CHECK (
        state IN ('reserved', 'delivered')
    )
);

-- Receiver-side launch reservation.  It exists before a runtime session and
-- therefore closes the old launch-before-campaign crash/duplicate window.
CREATE TABLE IF NOT EXISTS a2a_launch_reservations (
    id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    workflow_id UUID NOT NULL,
    task_id TEXT NOT NULL UNIQUE,
    campaign_id UUID NOT NULL,
    session_id TEXT,
    state TEXT NOT NULL DEFAULT 'reserved',
    error TEXT NOT NULL DEFAULT '',
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_token UUID,
    fencing_generation BIGINT NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (owner_id, tenant_id, message_id),
    CONSTRAINT a2a_launch_reservations_state_check CHECK (
        state IN ('reserved', 'launching', 'launched', 'failed')
    )
);

CREATE INDEX IF NOT EXISTS idx_a2a_launch_reservations_reconcile
    ON a2a_launch_reservations (state, lease_expires_at)
    WHERE state IN ('reserved', 'launching');
