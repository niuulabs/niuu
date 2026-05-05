import { Modal, StateDot } from '@niuulabs/ui';
import type { Workflow } from '../domain/workflow';
import { useWorkflows } from './useWorkflows';

export interface SagaWorkflowModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sagaName: string;
  onAssign: (workflow: Workflow) => void;
}

function countStages(workflow: Workflow): number {
  return workflow.nodes.filter((node) => node.kind === 'stage').length;
}

export function SagaWorkflowModal({
  open,
  onOpenChange,
  sagaName,
  onAssign,
}: SagaWorkflowModalProps) {
  const { data: workflows, isLoading, isError } = useWorkflows();

  function handleSelect(workflow: Workflow) {
    onAssign(workflow);
    onOpenChange(false);
  }

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Assign workflow"
      description={`Choose the default workflow for ${sagaName}. Dispatch can still override it per run.`}
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
          {workflows.map((workflow: Workflow) => {
            const stages = countStages(workflow);
            return (
              <button
                key={workflow.id}
                type="button"
                onClick={() => handleSelect(workflow)}
                className="niuu-flex niuu-items-center niuu-justify-between niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-tertiary hover:niuu-bg-bg-elevated niuu-px-3 niuu-py-2.5 niuu-text-left niuu-transition-colors"
              >
                <div>
                  <div className="niuu-text-sm niuu-font-medium niuu-text-text-primary">
                    {workflow.name}
                  </div>
                  <div className="niuu-mt-0.5 niuu-font-mono niuu-text-xs niuu-text-text-muted">
                    {stages} stage{stages !== 1 ? 's' : ''}
                  </div>
                </div>
                {workflow.version && (
                  <span className="niuu-rounded-full niuu-bg-bg-elevated niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
                    v{workflow.version}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </Modal>
  );
}
