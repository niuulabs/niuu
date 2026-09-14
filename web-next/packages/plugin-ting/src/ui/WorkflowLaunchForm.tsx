/**
 * WorkflowLaunchForm — the fields a workflow launch needs, plus their draft state.
 *
 * Two surfaces launch a workflow: the builder's launch modal and the Simple-mode
 * workflows page. Both collect the same four values, so the fields and the draft
 * live here and each surface supplies its own frame (modal chrome vs. card) and
 * submit button.
 *
 * Owner: plugin-ting.
 */

import { useState } from 'react';
import { BranchSelect, RepoSelect, cn, type RepoRecord } from '@niuulabs/ui';
import type { WorkflowLaunchRequest } from '../ports';

export interface WorkflowLaunchDraft {
  prompt: string;
  sessionName: string;
  repo: string;
  branch: string;
  error: string;
}

const EMPTY_DRAFT: WorkflowLaunchDraft = {
  prompt: '',
  sessionName: '',
  repo: '',
  branch: '',
  error: '',
};

export interface WorkflowLaunchDraftHandle {
  values: WorkflowLaunchDraft;
  update: (patch: Partial<WorkflowLaunchDraft>) => void;
  reset: () => void;
}

/**
 * Draft state for one workflow.
 *
 * `workflowKey` identifies the workflow the draft belongs to: when it changes,
 * the draft falls back to `initial` instead of carrying another workflow's text.
 */
export function useWorkflowLaunchDraft(
  workflowKey: string,
  initial?: Partial<WorkflowLaunchDraft>,
): WorkflowLaunchDraftHandle {
  const [draft, setDraft] = useState<(WorkflowLaunchDraft & { workflowKey: string }) | null>(null);
  const values =
    draft?.workflowKey === workflowKey ? draft : { ...EMPTY_DRAFT, ...initial, workflowKey };

  return {
    values,
    update(patch) {
      setDraft((prev) => ({
        ...(prev?.workflowKey === workflowKey ? prev : values),
        ...patch,
        workflowKey,
      }));
    },
    reset() {
      setDraft(null);
    },
  };
}

/** The launch request a draft encodes, with blank optional fields omitted. */
export function workflowLaunchRequest(values: WorkflowLaunchDraft): WorkflowLaunchRequest {
  return {
    prompt: values.prompt.trim(),
    ...(values.sessionName.trim() ? { sessionName: values.sessionName.trim() } : {}),
    ...(values.repo.trim() ? { repo: values.repo.trim() } : {}),
    ...(values.branch.trim() ? { branch: values.branch.trim() } : {}),
  };
}

export interface WorkflowLaunchFormProps {
  values: WorkflowLaunchDraft;
  onChange: (patch: Partial<WorkflowLaunchDraft>) => void;
  repos?: RepoRecord[];
  /** Show the optional session-name override. */
  showSessionName?: boolean;
  promptLabel?: string;
  promptPlaceholder?: string;
  /** Lines the prompt box starts with; the launch modal keeps six, the page three. */
  promptRows?: number;
  onPromptSubmit?: () => void;
}

const FIELD_CLASS =
  'niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary';

export function WorkflowLaunchForm({
  values,
  onChange,
  repos = [],
  showSessionName = true,
  promptLabel = 'Prompt',
  promptPlaceholder = 'Describe what this workflow should do.',
  promptRows = 6,
  onPromptSubmit,
}: WorkflowLaunchFormProps) {
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-4">
      <label className="niuu:flex niuu:flex-col niuu:gap-1.5">
        <span className="niuu:text-xs niuu:font-semibold niuu:text-text-primary">
          {promptLabel}
        </span>
        <textarea
          data-testid="workflow-launch-prompt"
          value={values.prompt}
          onChange={(event) => onChange({ prompt: event.target.value })}
          onKeyDown={(event) => {
            if (!onPromptSubmit) return;
            if (event.key !== 'Enter' || !(event.metaKey || event.ctrlKey)) return;
            event.preventDefault();
            onPromptSubmit();
          }}
          rows={promptRows}
          placeholder={promptPlaceholder}
          className={cn(promptRows >= 6 ? 'niuu:min-h-[132px]' : 'niuu:min-h-[76px]', FIELD_CLASS)}
        />
      </label>

      <div className="niuu:grid niuu:grid-cols-2 niuu:gap-3">
        {showSessionName ? (
          <label className="niuu:flex niuu:flex-col niuu:gap-1.5">
            <span className="niuu:text-xs niuu:font-semibold niuu:text-text-primary">
              Session name
            </span>
            <input
              data-testid="workflow-launch-session-name"
              value={values.sessionName}
              onChange={(event) => onChange({ sessionName: event.target.value })}
              placeholder="Optional override"
              className={FIELD_CLASS}
            />
          </label>
        ) : null}

        <label className="niuu:flex niuu:flex-col niuu:gap-1.5">
          <span className="niuu:text-xs niuu:font-semibold niuu:text-text-primary">Repository</span>
          {repos.length > 0 ? (
            <RepoSelect
              repos={repos}
              value={values.repo}
              onChange={(value) => {
                const selectedRepo = repos.find((item) => item.cloneUrl === value);
                onChange({ repo: value, branch: selectedRepo?.defaultBranch ?? '' });
              }}
              placeholder="Select repository"
              valueMode="cloneUrl"
              testId="workflow-launch-repo-select"
            />
          ) : (
            <input
              data-testid="workflow-launch-repo"
              value={values.repo}
              onChange={(event) => onChange({ repo: event.target.value })}
              placeholder="Optional repo or org/repo"
              className={FIELD_CLASS}
            />
          )}
        </label>

        <label className="niuu:flex niuu:flex-col niuu:gap-1.5">
          <span className="niuu:text-xs niuu:font-semibold niuu:text-text-primary">Branch</span>
          {values.repo && repos.length > 0 ? (
            <BranchSelect
              repos={repos}
              selectedRepos={values.repo}
              value={values.branch}
              onChange={(value) => onChange({ branch: value })}
              placeholder="Select branch"
              testId="workflow-launch-branch-select"
            />
          ) : (
            <input
              data-testid="workflow-launch-branch"
              value={values.branch}
              onChange={(event) => onChange({ branch: event.target.value })}
              placeholder="Optional branch"
              className={FIELD_CLASS}
            />
          )}
        </label>
      </div>

      {values.error ? (
        <p className="niuu:m-0 niuu:text-sm niuu:text-critical" role="alert">
          {values.error}
        </p>
      ) : null}
    </div>
  );
}
