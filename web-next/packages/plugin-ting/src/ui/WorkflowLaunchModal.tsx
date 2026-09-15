import { useEffect, useMemo } from 'react';
import { Modal } from '@niuulabs/ui';
import { type RepoRecord } from '@niuulabs/ui';
import type { Workflow } from '../domain/workflow';
import type { WorkflowLaunchRequest } from '../ports';
import {
  WorkflowLaunchForm,
  useWorkflowLaunchDraft,
  workflowLaunchRequest,
} from './WorkflowLaunchForm';

export interface WorkflowLaunchModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflow: Workflow | null;
  repos?: RepoRecord[];
  loadBranches?: (repoUrl: string) => Promise<string[]>;
  launching?: boolean;
  onLaunch: (request: WorkflowLaunchRequest) => Promise<void> | void;
}

export function WorkflowLaunchModal({
  open,
  onOpenChange,
  workflow,
  repos = [],
  loadBranches,
  launching = false,
  onLaunch,
}: WorkflowLaunchModalProps) {
  const workflowKey = workflow?.id ?? 'none';
  const draft = useWorkflowLaunchDraft(workflowKey);
  const { reset } = draft;

  useEffect(() => {
    if (!open) {
      queueMicrotask(() => {
        reset();
      });
    }
  }, [open, reset]);

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      draft.reset();
    }
    onOpenChange(nextOpen);
  }

  const canLaunch = useMemo(
    () => draft.values.prompt.trim().length > 0 && workflow !== null && !launching,
    [draft.values.prompt, launching, workflow],
  );

  async function handleLaunch() {
    if (!workflow) return;

    draft.update({ error: '' });
    try {
      await onLaunch(workflowLaunchRequest(draft.values));
      draft.reset();
    } catch (launchError) {
      draft.update({
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
      <div className="niuu:mt-4">
        <WorkflowLaunchForm
          values={draft.values}
          onChange={draft.update}
          repos={repos}
          loadBranches={loadBranches}
        />
      </div>
    </Modal>
  );
}
