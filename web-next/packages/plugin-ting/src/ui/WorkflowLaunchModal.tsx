import { useEffect, useMemo, useState } from 'react';
import { Modal } from '@niuulabs/ui';
import { BranchSelect, RepoSelect, type RepoRecord } from '@niuulabs/ui';
import type { Workflow } from '../domain/workflow';
import type { WorkflowLaunchRequest } from '../ports';

export interface WorkflowLaunchModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflow: Workflow | null;
  repos?: RepoRecord[];
  launching?: boolean;
  onLaunch: (request: WorkflowLaunchRequest) => Promise<void> | void;
}

export function WorkflowLaunchModal({
  open,
  onOpenChange,
  workflow,
  repos = [],
  launching = false,
  onLaunch,
}: WorkflowLaunchModalProps) {
  const workflowKey = workflow?.id ?? 'none';
  const [draft, setDraft] = useState<{
    workflowKey: string;
    prompt: string;
    sessionName: string;
    repo: string;
    branch: string;
    error: string;
  } | null>(null);
  const current =
    draft?.workflowKey === workflowKey
      ? draft
      : {
          workflowKey,
          prompt: '',
          sessionName: '',
          repo: '',
          branch: '',
          error: '',
        };

  useEffect(() => {
    if (!open) {
      queueMicrotask(() => {
        setDraft(null);
      });
    }
  }, [open]);

  function updateDraft(patch: Partial<Omit<NonNullable<typeof draft>, 'workflowKey'>>) {
    setDraft((prev) => ({
      ...(prev?.workflowKey === workflowKey ? prev : current),
      ...patch,
      workflowKey,
    }));
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      setDraft(null);
    }
    onOpenChange(nextOpen);
  }

  const canLaunch = useMemo(
    () => current.prompt.trim().length > 0 && workflow !== null && !launching,
    [current.prompt, launching, workflow],
  );

  async function handleLaunch() {
    if (!workflow) return;

    updateDraft({ error: '' });
    try {
      await onLaunch({
        prompt: current.prompt.trim(),
        ...(current.sessionName.trim() ? { sessionName: current.sessionName.trim() } : {}),
        ...(current.repo.trim() ? { repo: current.repo.trim() } : {}),
        ...(current.branch.trim() ? { branch: current.branch.trim() } : {}),
      });
      setDraft(null);
    } catch (launchError) {
      updateDraft({
        error: launchError instanceof Error ? launchError.message : 'Launch failed.',
      });
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={handleOpenChange}
      title={workflow ? `Launch ${workflow.name}` : 'Launch workflow'}
      description="Start this workflow directly in Volundr as a workflow-backed flock session."
      actions={[
        { label: 'Cancel', variant: 'secondary' },
        {
          label: launching ? 'Launching…' : 'Launch',
          variant: 'primary',
          onClick: handleLaunch,
          closes: false,
          disabled: !canLaunch,
        },
      ]}
    >
      <div className="niuu-mt-4 niuu-flex niuu-flex-col niuu-gap-4">
        <label className="niuu-flex niuu-flex-col niuu-gap-1.5">
          <span className="niuu-text-xs niuu-font-semibold niuu-text-text-primary">Prompt</span>
          <textarea
            value={current.prompt}
            onChange={(event) => updateDraft({ prompt: event.target.value })}
            rows={6}
            placeholder="Describe what this workflow should do."
            className="niuu-min-h-[132px] niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary"
          />
        </label>

        <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3">
          <label className="niuu-flex niuu-flex-col niuu-gap-1.5">
            <span className="niuu-text-xs niuu-font-semibold niuu-text-text-primary">
              Session name
            </span>
            <input
              value={current.sessionName}
              onChange={(event) => updateDraft({ sessionName: event.target.value })}
              placeholder="Optional override"
              className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary"
            />
          </label>
          <label className="niuu-flex niuu-flex-col niuu-gap-1.5">
            <span className="niuu-text-xs niuu-font-semibold niuu-text-text-primary">Repo</span>
            {repos.length > 0 ? (
              <RepoSelect
                repos={repos}
                value={current.repo}
                onChange={(value) => {
                  const selectedRepo = repos.find((item) => item.cloneUrl === value);
                  updateDraft({ repo: value, branch: selectedRepo?.defaultBranch ?? '' });
                }}
                placeholder="Select repository"
                valueMode="cloneUrl"
                testId="workflow-launch-repo-select"
              />
            ) : (
              <input
                value={current.repo}
                onChange={(event) => updateDraft({ repo: event.target.value })}
                placeholder="Optional repo or org/repo"
                className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary"
              />
            )}
          </label>
          <label className="niuu-flex niuu-flex-col niuu-gap-1.5">
            <span className="niuu-text-xs niuu-font-semibold niuu-text-text-primary">Branch</span>
            {current.repo && repos.length > 0 ? (
              <BranchSelect
                repos={repos}
                selectedRepos={current.repo}
                value={current.branch}
                onChange={(value) => updateDraft({ branch: value })}
                placeholder="Select branch"
                testId="workflow-launch-branch-select"
              />
            ) : (
              <input
                value={current.branch}
                onChange={(event) => updateDraft({ branch: event.target.value })}
                placeholder="Optional branch"
                className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary"
              />
            )}
          </label>
        </div>

        {current.error ? (
          <p className="niuu-m-0 niuu-text-sm niuu-text-critical" role="alert">
            {current.error}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
