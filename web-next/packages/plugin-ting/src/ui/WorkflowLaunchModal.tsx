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
  const [prompt, setPrompt] = useState('');
  const [sessionName, setSessionName] = useState('');
  const [repo, setRepo] = useState('');
  const [branch, setBranch] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setPrompt('');
    setSessionName('');
    setRepo('');
    setBranch('');
    setError('');
  }, [open, workflow?.id]);

  const canLaunch = useMemo(
    () => prompt.trim().length > 0 && workflow !== null && !launching,
    [launching, prompt, workflow],
  );

  async function handleLaunch() {
    if (!workflow) return;

    setError('');
    try {
      await onLaunch({
        prompt: prompt.trim(),
        ...(sessionName.trim() ? { sessionName: sessionName.trim() } : {}),
        ...(repo.trim() ? { repo: repo.trim() } : {}),
        ...(branch.trim() ? { branch: branch.trim() } : {}),
      });
    } catch (launchError) {
      setError(launchError instanceof Error ? launchError.message : 'Launch failed.');
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
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
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
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
              value={sessionName}
              onChange={(event) => setSessionName(event.target.value)}
              placeholder="Optional override"
              className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary"
            />
          </label>
          <label className="niuu-flex niuu-flex-col niuu-gap-1.5">
            <span className="niuu-text-xs niuu-font-semibold niuu-text-text-primary">Repo</span>
            {repos.length > 0 ? (
              <RepoSelect
                repos={repos}
                value={repo}
                onChange={(value) => {
                  const selectedRepo = repos.find((item) => item.cloneUrl === value);
                  setRepo(value);
                  setBranch(selectedRepo?.defaultBranch ?? '');
                }}
                placeholder="Select repository"
                valueMode="cloneUrl"
                testId="workflow-launch-repo-select"
              />
            ) : (
              <input
                value={repo}
                onChange={(event) => setRepo(event.target.value)}
                placeholder="Optional repo or org/repo"
                className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary"
              />
            )}
          </label>
          <label className="niuu-flex niuu-flex-col niuu-gap-1.5">
            <span className="niuu-text-xs niuu-font-semibold niuu-text-text-primary">Branch</span>
            {repo && repos.length > 0 ? (
              <BranchSelect
                repos={repos}
                selectedRepos={repo}
                value={branch}
                onChange={setBranch}
                placeholder="Select branch"
                testId="workflow-launch-branch-select"
              />
            ) : (
              <input
                value={branch}
                onChange={(event) => setBranch(event.target.value)}
                placeholder="Optional branch"
                className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary"
              />
            )}
          </label>
        </div>

        {error ? (
          <p className="niuu-m-0 niuu-text-sm niuu-text-critical" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
