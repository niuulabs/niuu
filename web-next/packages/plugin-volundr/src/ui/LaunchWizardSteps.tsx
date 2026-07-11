import { BranchSelect, Field, Input, RepoSelect, type RepoRecord } from '@niuulabs/ui';
import type { IntegrationConnection, SessionDefinition, TrackerIssue, VolundrTarget } from '../models/volundr.model';
import {
  deriveSessionName,
  formatIntegrationLabel,
  formatModelOption,
  normalizeRepoUrl,
  type RuntimeModelDescriptor,
  type WizardForm,
} from './launchWizardModel';
import { BOOT_STEPS, SECONDARY_BUTTON_CLASS, SectionCard } from './LaunchWizardPrimitives';
import './LaunchWizard.css';

export * from './LaunchWizardPrimitives';
export { RuntimeStep } from './LaunchWizardRuntimeStep';

export function SourceStep({
  form,
  update,
  repos,
  branchOptions,
  trackerResults,
  trackerLoading,
}: {
  form: WizardForm;
  update: (patch: Partial<WizardForm>) => void;
  repos: RepoRecord[];
  branchOptions: string[];
  trackerResults: TrackerIssue[];
  trackerLoading: boolean;
}) {
  const currentRepo = repos.find((repo) => repo.cloneUrl === form.repo);

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-4" data-testid="step-source-content">
      <SectionCard
        title="Workspace source"
        description="Choose where the session should start from and attach tracker context if needed."
      >
        <div className="niuu:flex niuu:gap-2">
          {(['git', 'local_mount', 'blank'] as const).map((t) => (
            <button
              key={t}
              className={`niuu:rounded-md niuu:border niuu:px-3 niuu:py-2 niuu:text-xs ${
                form.sourcetype === t
                  ? 'niuu:border-brand niuu:bg-bg-tertiary niuu:text-text-primary'
                  : 'niuu:border-border-subtle niuu:bg-bg-primary niuu:text-text-secondary niuu:hover:border-brand'
              }`}
              onClick={() => update({ sourcetype: t })}
              data-testid={`source-tab-${t}`}
            >
              {t === 'local_mount' ? 'local mount' : t}
            </button>
          ))}
        </div>
        {form.sourcetype === 'git' ? (
          <div className="niuu:grid niuu:grid-cols-2 niuu:gap-4">
            <Field label="Repository">
              {repos.length > 0 ? (
                <RepoSelect
                  repos={repos}
                  value={form.repo}
                  onChange={(value: string) => {
                    const repo = repos.find((item) => item.cloneUrl === value);
                    update({
                      repo: value,
                      branch: repo?.defaultBranch ?? '',
                      workspaceId: '',
                    });
                  }}
                  placeholder="Select repository"
                  testId="repo-select"
                />
              ) : (
                <Input
                  value={form.repo}
                  onChange={(e) => update({ repo: e.target.value, workspaceId: '' })}
                  placeholder="github.com/niuulabs/volundr"
                />
              )}
            </Field>
            <Field label="Branch">
              {branchOptions.length ? (
                currentRepo?.branches.length ? (
                  <BranchSelect
                    repos={repos}
                    selectedRepos={form.repo}
                    value={form.branch}
                    onChange={(value: string) => update({ branch: value })}
                    placeholder="Select branch"
                    testId="branch-select"
                  />
                ) : (
                  <WizardSelect
                    options={branchOptions.map((branch) => ({ value: branch, label: branch }))}
                    value={form.branch}
                    onChange={(value) => update({ branch: value })}
                    placeholder="Select branch"
                    testId="branch-select"
                  />
                )
              ) : (
                <Input
                  value={form.branch}
                  onChange={(e) => update({ branch: e.target.value })}
                  placeholder="main"
                />
              )}
            </Field>
          </div>
        ) : null}
        {form.sourcetype === 'local_mount' ? (
          <Field label="Path">
            <Input
              value={form.mountPath}
              onChange={(e) => update({ mountPath: e.target.value })}
              placeholder="~/code/niuu"
            />
          </Field>
        ) : null}
        {form.sourcetype === 'blank' ? (
          <p className="niuu:font-mono niuu:text-xs niuu:text-text-faint">
            Pod will boot with empty /workspace
          </p>
        ) : null}
        <Field label="Session name (optional)">
          <Input
            value={form.sessionName}
            onChange={(e) => update({ sessionName: e.target.value })}
            placeholder="auto-generated from branch if blank"
          />
        </Field>
        <div className="niuu:flex niuu:flex-col niuu:gap-2">
          <Field label="Tracker issue (optional)">
            <Input
              value={form.trackerQuery}
              onChange={(e) => update({ trackerQuery: e.target.value })}
              placeholder="Search tracker issues"
            />
          </Field>
          {form.trackerIssue ? (
            <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-secondary">
              linked: <span className="niuu:font-mono">{form.trackerIssue.identifier}</span> ·{' '}
              {form.trackerIssue.title}
              <button
                type="button"
                className="niuu:ml-3 niuu:text-text-faint niuu:hover:text-text-primary"
                onClick={() => update({ trackerIssue: null, trackerQuery: '' })}
              >
                clear
              </button>
            </div>
          ) : null}
          {trackerLoading ? (
            <div className="niuu:text-xs niuu:text-text-faint">searching…</div>
          ) : trackerResults.length > 0 ? (
            <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
              {trackerResults.slice(0, 6).map((issue) => (
                <button
                  key={issue.id}
                  type="button"
                  className={`${SECONDARY_BUTTON_CLASS} niuu:text-left`}
                  onClick={() => update({ trackerIssue: issue, trackerQuery: issue.identifier })}
                >
                  <div className="niuu:font-mono niuu:text-text-primary">{issue.identifier}</div>
                  <div className="niuu:text-text-muted">{issue.title}</div>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </SectionCard>
    </div>
  );
}

export function ConfirmStep({
  form,
  models,
  integrations,
  sessionDefinitions,
  targets,
}: {
  form: WizardForm;
  models: Record<string, RuntimeModelDescriptor>;
  integrations: IntegrationConnection[];
  sessionDefinitions: SessionDefinition[];
  targets: VolundrTarget[];
}) {
  const modelLabel = formatModelOption(form.model, models[form.model]);
  const definitionLabel =
    sessionDefinitions.find((d) => d.key === form.definition)?.displayName ?? form.definition;
  const targetLabel =
    form.targetMode === 'tags'
      ? `tags(${form.targetMatch}): ${form.targetTags.join(', ')}`
      : targets.find((target) => target.id === form.instanceId)?.name ||
        form.instanceId ||
        'default';
  const integrationLabels = form.selectedIntegrations.map((id) => {
    const integration = integrations.find((item) => item.id === id);
    return integration ? formatIntegrationLabel(integration) : id;
  });
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-4" data-testid="step-confirm-content">
      <SectionCard
        title="Launch summary"
        description="Final review before Forge provisions the session."
      >
        <div className="niuu:flex niuu:flex-col niuu:divide-y niuu:divide-border-subtle">
          <ConfirmRow label="session" value={deriveSessionName(form)} />
          <ConfirmRow label="forge" value={targetLabel} />
          <ConfirmRow label="definition" value={definitionLabel} />
          <ConfirmRow label="model" value={modelLabel} />
          <ConfirmRow
            label="source"
            value={
              form.sourcetype === 'git'
                ? `${form.repo}@${form.branch}`
                : form.sourcetype === 'local_mount'
                  ? form.mountPath
                  : 'blank'
            }
          />
          <ConfirmRow
            label="resources"
            value={`${form.cpu}c \u00B7 ${form.mem}${form.gpu !== '0' ? ` \u00B7 gpu ${form.gpu}` : ''}`}
          />
          <ConfirmRow label="workspace" value={form.workspaceId || 'new'} />
          <ConfirmRow
            label="tracker"
            value={
              form.trackerIssue
                ? `${form.trackerIssue.identifier} · ${form.trackerIssue.title}`
                : 'none'
            }
          />
        </div>
      </SectionCard>

      <div className="niuu:grid niuu:grid-cols-2 niuu:gap-4">
        <SectionCard
          title="Attached access"
          description="Secrets and integrations that will be available immediately."
        >
          <ConfirmChipList
            title="Credentials"
            items={form.selectedCredentials}
            emptyLabel="No credentials attached"
          />
          <ConfirmChipList
            title="Integrations"
            items={integrationLabels}
            emptyLabel="No integrations attached"
          />
        </SectionCard>

        <SectionCard
          title="Advanced runtime"
          description="Additional runtime wiring and bootstrap instructions."
        >
          <ConfirmChipList
            title="MCP servers"
            items={form.mcpServers.map((server) => server.name)}
            emptyLabel="No MCP servers attached"
          />
          <ConfirmChipList
            title="Environment"
            items={form.envVars
              .filter((entry) => entry.key.trim())
              .map((entry) => `${entry.key}=${entry.value}`)}
            emptyLabel="No custom environment variables"
          />
          <ConfirmChipList
            title="Setup scripts"
            items={form.setupScripts.filter((script) => script.trim())}
            emptyLabel="No setup scripts"
          />
          {form.systemPrompt.trim() || form.initialPrompt.trim() ? (
            <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3">
              <div className="niuu:text-xs niuu:text-text-faint">Prompt overrides</div>
              {form.systemPrompt.trim() ? (
                <div className="niuu:mt-2 niuu:text-xs niuu:text-text-secondary">
                  <span className="niuu:font-mono niuu:text-text-primary">system</span> ·{' '}
                  {form.systemPrompt}
                </div>
              ) : null}
              {form.initialPrompt.trim() ? (
                <div className="niuu:mt-2 niuu:text-xs niuu:text-text-secondary">
                  <span className="niuu:font-mono niuu:text-text-primary">initial</span> ·{' '}
                  {form.initialPrompt}
                </div>
              ) : null}
            </div>
          ) : null}
        </SectionCard>
      </div>
    </div>
  );
}

export function ConfirmRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="niuu:flex niuu:items-center niuu:gap-4 niuu:py-2" data-testid="confirm-row">
      <span className="niuu:w-24 niuu:font-mono niuu:text-xs niuu:text-text-faint">{label}</span>
      <span className="niuu:font-mono niuu:text-sm niuu:text-text-primary">{value}</span>
    </div>
  );
}

export function ConfirmChipList({
  title,
  items,
  emptyLabel,
}: {
  title: string;
  items: string[];
  emptyLabel: string;
}) {
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-2">
      <div className="niuu:text-xs niuu:text-text-faint">{title}</div>
      {items.length > 0 ? (
        <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
          {items.map((item) => (
            <span
              key={item}
              className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-2.5 niuu:py-1 niuu:font-mono niuu:text-xs niuu:text-text-secondary"
            >
              {item}
            </span>
          ))}
        </div>
      ) : (
        <div className="niuu:text-xs niuu:text-text-faint">{emptyLabel}</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step: Booting
// ---------------------------------------------------------------------------

export function BootingStep({ bootStep, progress }: { bootStep: number; progress: number }) {
  return (
    <div
      className="niuu:flex niuu:flex-col niuu:items-center niuu:gap-6 niuu:py-4"
      data-testid="step-booting-content"
    >
      {/* Anvil SVG */}
      <svg viewBox="0 0 200 80" className="niuu:h-20 niuu:w-48" aria-hidden>
        <rect x="70" y="48" width="60" height="10" rx="1" fill="var(--brand-500)" />
        <rect
          x="80"
          y="58"
          width="40"
          height="8"
          rx="1"
          fill="var(--brand-600, var(--brand-500))"
        />
        <rect
          x="90"
          y="66"
          width="20"
          height="10"
          rx="1"
          fill="var(--brand-700, var(--brand-500))"
        />
        <rect x="92" y="30" width="16" height="18" rx="2" fill="var(--brand-400)" opacity="0.7">
          <animate attributeName="opacity" values="0.6;1;0.7" dur="1.6s" repeatCount="indefinite" />
        </rect>
        <circle cx="70" cy="20" r="1.2" fill="var(--brand-300, var(--brand-500))">
          <animate attributeName="cy" values="20;4;20" dur="2s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="1;0;0" dur="2s" repeatCount="indefinite" />
        </circle>
        <circle cx="100" cy="20" r="1.5" fill="var(--brand-400)">
          <animate attributeName="cy" values="20;0;20" dur="1.6s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="1;0;0" dur="1.6s" repeatCount="indefinite" />
        </circle>
        <circle cx="130" cy="20" r="1.2" fill="var(--brand-300, var(--brand-500))">
          <animate attributeName="cy" values="20;6;20" dur="2.4s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="1;0;0" dur="2.4s" repeatCount="indefinite" />
        </circle>
      </svg>
      {/* Progress bar */}
      <div
        className="niuu:w-full niuu:h-1.5 niuu:rounded-full niuu:bg-bg-elevated"
        role="progressbar"
        aria-valuenow={Math.round(progress * 100)}
        aria-valuemax={100}
      >
        <div
          className="niuu:h-full niuu:rounded-full niuu:bg-brand niuu:transition-all"
          style={{ width: `${(progress * 100).toFixed(0)}%` }}
        />
      </div>
      {/* Step list */}
      <div className="niuu:flex niuu:flex-col niuu:gap-2 niuu:w-full">
        {BOOT_STEPS.map((step, i) => (
          <div
            key={step.id}
            className={`niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs ${
              i < bootStep
                ? 'niuu:text-text-muted'
                : i === bootStep
                  ? 'niuu:text-brand'
                  : 'niuu:text-text-faint'
            }`}
            data-testid="boot-step"
          >
            <span className="niuu:w-4 niuu:text-center niuu:font-mono">
              {i < bootStep ? '\u2713' : i === bootStep ? '\u2026' : '\u25CB'}
            </span>
            <span className="niuu:font-mono">{step.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// LaunchWizard
// ---------------------------------------------------------------------------

/** 4-step modal wizard for launching new Volundr sessions. */
