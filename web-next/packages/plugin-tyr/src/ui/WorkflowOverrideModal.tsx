import { Modal, StateDot } from '@niuulabs/ui';
import { useWorkflows } from './useWorkflows';
import type { Workflow } from '../domain/workflow';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface WorkflowOverrideModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selectedCount: number;
  onApply: (workflow: Workflow) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function countStages(workflow: Workflow): number {
  return workflow.nodes.filter((n) => n.kind === 'stage').length;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function WorkflowOverrideModal({
  open,
  onOpenChange,
  selectedCount,
  onApply,
}: WorkflowOverrideModalProps) {
  const { data: workflows, isLoading, isError } = useWorkflows();

  function handleSelect(wf: Workflow) {
    onApply(wf);
    onOpenChange(false);
  }

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Apply workflow override"
      description={`Override the workflow for ${selectedCount} selected raid${selectedCount !== 1 ? 's' : ''}. The saga's original workflow is preserved; this applies only to this dispatch.`}
      actions={[{ label: 'Cancel', variant: 'secondary' }]}
    >
      {isLoading && (
        <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-py-4">
          <StateDot state="processing" pulse />
          <span className="niuu-text-sm niuu-text-text-muted">Loading workflows…</span>
        </div>
      )}
      {isError && (
        <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-py-4">
          <StateDot state="failed" />
          <span className="niuu-text-sm niuu-text-text-muted">Failed to load workflows.</span>
        </div>
      )}
      {!isLoading && !isError && (!workflows || workflows.length === 0) && (
        <p className="niuu-py-4 niuu-text-sm niuu-text-text-muted">No workflows available.</p>
      )}
      {!isLoading && !isError && workflows && workflows.length > 0 && (
        <div className="niuu-mt-3 niuu-flex niuu-flex-col niuu-gap-1">
          {workflows.map((wf) => {
            const stages = countStages(wf);
            return (
              <button
                key={wf.id}
                type="button"
                onClick={() => handleSelect(wf)}
                className="niuu-flex niuu-items-center niuu-justify-between niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-tertiary hover:niuu-bg-bg-elevated niuu-px-3 niuu-py-2.5 niuu-text-left niuu-transition-colors"
              >
                <div>
                  <div className="niuu-text-sm niuu-font-medium niuu-text-text-primary">
                    {wf.name}
                  </div>
                  <div className="niuu-mt-0.5 niuu-font-mono niuu-text-xs niuu-text-text-muted">
                    {stages} stage{stages !== 1 ? 's' : ''}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </Modal>
  );
}
