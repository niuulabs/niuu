import { useEffect, useMemo, useState } from 'react';
import { Modal } from '@niuulabs/ui';
import type { Workflow } from '../domain/workflow';
import type { WorkflowLaunchRequest } from '../ports';

export interface WorkflowLaunchModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflow: Workflow | null;
  launching?: boolean;
  onLaunch: (request: WorkflowLaunchRequest) => Promise<void> | void;
}

function prettyContext(value: Record<string, unknown> | undefined): string {
  if (!value || Object.keys(value).length === 0) return '';
  return JSON.stringify(value, null, 2);
}

export function WorkflowLaunchModal({
  open,
  onOpenChange,
  workflow,
  launching = false,
  onLaunch,
}: WorkflowLaunchModalProps) {
  const [prompt, setPrompt] = useState('');
  const [sessionName, setSessionName] = useState('');
  const [repo, setRepo] = useState('');
  const [branch, setBranch] = useState('');
  const [model, setModel] = useState('');
  const [mimirPath, setMimirPath] = useState('');
  const [contextText, setContextText] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setPrompt('');
    setSessionName('');
    setRepo('');
    setBranch('');
    setModel('');
    setMimirPath('');
    setContextText('');
    setError('');
  }, [open, workflow?.id]);

  const canLaunch = useMemo(
    () => prompt.trim().length > 0 && workflow !== null && !launching,
    [launching, prompt, workflow],
  );

  async function handleLaunch() {
    if (!workflow) return;
    if (!prompt.trim()) {
      setError('Prompt is required.');
      return;
    }

    let context: Record<string, unknown> | undefined;
    if (contextText.trim()) {
      try {
        const parsed = JSON.parse(contextText);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          setError('Context must be a JSON object.');
          return;
        }
        context = parsed as Record<string, unknown>;
      } catch {
        setError('Context must be valid JSON.');
        return;
      }
    }

    setError('');
    try {
      await onLaunch({
        prompt: prompt.trim(),
        ...(sessionName.trim() ? { sessionName: sessionName.trim() } : {}),
        ...(repo.trim() ? { repo: repo.trim() } : {}),
        ...(branch.trim() ? { branch: branch.trim() } : {}),
        ...(model.trim() ? { model: model.trim() } : {}),
        ...(mimirPath.trim() ? { mimirPath: mimirPath.trim() } : {}),
        ...(context ? { context } : {}),
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
            <span className="niuu-text-xs niuu-font-semibold niuu-text-text-primary">Model</span>
            <input
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="Optional model override"
              className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary"
            />
          </label>
          <label className="niuu-flex niuu-flex-col niuu-gap-1.5">
            <span className="niuu-text-xs niuu-font-semibold niuu-text-text-primary">Repo</span>
            <input
              value={repo}
              onChange={(event) => setRepo(event.target.value)}
              placeholder="Optional repo or org/repo"
              className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary"
            />
          </label>
          <label className="niuu-flex niuu-flex-col niuu-gap-1.5">
            <span className="niuu-text-xs niuu-font-semibold niuu-text-text-primary">Branch</span>
            <input
              value={branch}
              onChange={(event) => setBranch(event.target.value)}
              placeholder="main"
              className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary"
            />
          </label>
          <label className="niuu-col-span-2 niuu-flex niuu-flex-col niuu-gap-1.5">
            <span className="niuu-text-xs niuu-font-semibold niuu-text-text-primary">
              Mimir path
            </span>
            <input
              value={mimirPath}
              onChange={(event) => setMimirPath(event.target.value)}
              placeholder="Optional resource path override"
              className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary"
            />
          </label>
        </div>

        <label className="niuu-flex niuu-flex-col niuu-gap-1.5">
          <span className="niuu-text-xs niuu-font-semibold niuu-text-text-primary">
            Structured context
          </span>
          <textarea
            value={contextText}
            onChange={(event) => setContextText(event.target.value)}
            rows={7}
            placeholder={prettyContext({
              mode: 'exploratory',
              constraints: ['Focus on demand and ethics.'],
              seed_urls: ['https://example.com'],
            })}
            className="niuu-min-h-[148px] niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-3 niuu-py-2 niuu-font-mono niuu-text-xs niuu-text-text-primary"
          />
        </label>

        {error ? (
          <p className="niuu-m-0 niuu-text-sm niuu-text-critical" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
