import { useState } from 'react';
import {
  autonomyModeForLevel,
  grantWorkflowName,
  isToolBuilderWorkflow,
  latestBuildGrant,
  BUILD_ACTION_CLASS,
  TOOL_BUILDER_TAG,
  TRUST_LEVELS,
  type AutonomyMode,
  type RealmSummary,
} from '../domain';
import {
  useCreateTrustGrant,
  useRealmTrustGrants,
  useToolWorkflows,
} from '../application/useRealmGovernance';

const MUTED = 'niuu:text-text-muted';
const CONTROL =
  'niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-primary';

function autonomyClasses(mode: AutonomyMode): string {
  if (mode === 'yolo') return 'niuu:text-state-warn';
  if (mode === 'autonomous') return 'niuu:text-state-ok';
  return 'niuu:text-text-secondary';
}

/**
 * The slug + display name the card needs. A full RealmSummary satisfies it,
 * and so does a realm reference derived from an environment id
 * (realmSlugForEnvironment) on the Valkyrie console.
 */
export type RealmRef = Pick<RealmSummary, 'slug' | 'name'>;

export function ToolBuilderGrantCard({ realm }: { realm: RealmRef }) {
  const grantsQuery = useRealmTrustGrants(realm.slug);
  const workflowsQuery = useToolWorkflows();
  const createGrant = useCreateTrustGrant(realm.slug);

  // null = follow whatever the server says; set once the operator touches a control.
  const [pickedWorkflow, setPickedWorkflow] = useState<string | null>(null);
  const [pickedLevel, setPickedLevel] = useState<number | null>(null);
  const [showAllWorkflows, setShowAllWorkflows] = useState(false);

  if (grantsQuery.isLoading || workflowsQuery.isLoading) {
    return (
      <div data-testid="tool-builder-loading" className={`niuu:text-xs ${MUTED}`}>
        Loading tool-builder grant…
      </div>
    );
  }

  const loadError = grantsQuery.error ?? workflowsQuery.error;
  if (loadError) {
    return (
      <div
        role="alert"
        data-testid="tool-builder-error"
        className="niuu:text-xs niuu:text-critical"
      >
        {loadError instanceof Error ? loadError.message : 'Unable to load the tool-builder grant'}
      </div>
    );
  }

  const grant = latestBuildGrant(grantsQuery.data ?? []);
  const grantedWorkflow = grantWorkflowName(grant);
  const workflows = workflowsQuery.data ?? [];
  const builderWorkflows = workflows.filter(isToolBuilderWorkflow);
  const visibleWorkflows = showAllWorkflows ? workflows : builderWorkflows;
  // Keep the currently granted workflow selectable even when it lost its tag.
  const options =
    grantedWorkflow && !visibleWorkflows.some((workflow) => workflow.name === grantedWorkflow)
      ? [...visibleWorkflows.map((workflow) => workflow.name), grantedWorkflow]
      : visibleWorkflows.map((workflow) => workflow.name);

  const selectedWorkflow = pickedWorkflow ?? grantedWorkflow;
  const selectedLevel = pickedLevel ?? grant?.level ?? 0;
  const dirty =
    selectedWorkflow !== grantedWorkflow || (grant ? selectedLevel !== grant.level : true);

  const save = () => {
    createGrant.mutate(
      {
        action_class: BUILD_ACTION_CLASS,
        target: '*',
        level: selectedLevel,
        limits: { workflow: selectedWorkflow },
        granted_by: 'human:operator',
      },
      {
        onSuccess: () => {
          setPickedWorkflow(null);
          setPickedLevel(null);
        },
      },
    );
  };

  const grantMode = grant ? autonomyModeForLevel(grant.level) : null;
  const selectedMode = autonomyModeForLevel(selectedLevel);

  return (
    <section
      data-testid="tool-builder-card"
      className="niuu:flex niuu:flex-col niuu:gap-2 niuu:border-t niuu:border-solid niuu:border-border-subtle niuu:pt-3"
    >
      <header className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <h3 className="niuu:text-xs niuu:font-medium niuu:text-text-primary">Tool Builder</h3>
        {grant && grantMode ? (
          <span
            data-testid="tool-builder-autonomy"
            className={`niuu:text-xs ${autonomyClasses(grantMode)}`}
          >
            {grantMode} · level {grant.level}
          </span>
        ) : (
          <span data-testid="tool-builder-ungranted" className={`niuu:text-xs ${MUTED}`}>
            no build grant yet
          </span>
        )}
      </header>
      {grant ? (
        <p className={`niuu:text-xs ${MUTED}`}>
          Builds run through{' '}
          <span className="niuu:text-text-secondary">
            {grantedWorkflow || 'no pinned workflow'}
          </span>
          {grant.granted_by ? ` · granted by ${grant.granted_by}` : ''}
        </p>
      ) : (
        <p className={`niuu:text-xs ${MUTED}`}>
          Grant <span className="niuu:text-text-secondary">build</span> trust to let this
          realm&apos;s Valkyrie commission new tools.
        </p>
      )}
      <label className={`niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs ${MUTED}`}>
        workflow
        <select
          aria-label={`Tool-builder workflow for ${realm.name}`}
          value={selectedWorkflow}
          disabled={createGrant.isPending}
          onChange={(event) => setPickedWorkflow(event.target.value)}
          className={`${CONTROL} niuu:min-w-0 niuu:flex-1`}
        >
          <option value="" disabled>
            Pick a workflow…
          </option>
          {options.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <div className={`niuu:flex niuu:items-center niuu:justify-between niuu:text-xs ${MUTED}`}>
        <span>
          {showAllWorkflows
            ? `showing all ${workflows.length} workflows`
            : `${builderWorkflows.length} tagged "${TOOL_BUILDER_TAG}"`}
        </span>
        <button
          type="button"
          onClick={() => setShowAllWorkflows((current) => !current)}
          className="niuu:cursor-pointer niuu:border-0 niuu:bg-transparent niuu:p-0 niuu:text-xs niuu:text-text-secondary niuu:underline"
        >
          {showAllWorkflows ? `show ${TOOL_BUILDER_TAG} only` : 'show all workflows'}
        </button>
      </div>
      <label className={`niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs ${MUTED}`}>
        trust level
        <select
          aria-label={`Build trust level for ${realm.name}`}
          value={String(selectedLevel)}
          disabled={createGrant.isPending}
          onChange={(event) => setPickedLevel(Number(event.target.value))}
          className={CONTROL}
        >
          {TRUST_LEVELS.map((level) => (
            <option key={level} value={String(level)}>
              {level} — {autonomyModeForLevel(level)}
            </option>
          ))}
        </select>
        <span data-testid="tool-builder-level-hint" className={autonomyClasses(selectedMode)}>
          {selectedMode}
        </span>
      </label>
      <p className={`niuu:text-xs ${MUTED}`}>0–1 guarded · 2–3 autonomous · 4–5 yolo</p>
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <button
          type="button"
          data-testid="tool-builder-save"
          disabled={createGrant.isPending || !selectedWorkflow || !dirty}
          onClick={save}
          className="niuu:cursor-pointer niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-xs niuu:text-text-primary niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50"
        >
          {createGrant.isPending ? 'Saving…' : 'Save grant'}
        </button>
        {createGrant.isError ? (
          <p
            role="alert"
            data-testid="tool-builder-save-error"
            className="niuu:text-xs niuu:text-critical"
          >
            {createGrant.error instanceof Error
              ? createGrant.error.message
              : 'Saving the grant failed'}
          </p>
        ) : null}
      </div>
    </section>
  );
}
