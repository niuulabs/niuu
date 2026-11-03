import { useState } from 'react';
import { ApiClientError } from '@niuulabs/query';
import { Modal } from '@niuulabs/ui';
import type { Saga, SagaBoardWorkflow } from '../domain/saga';
import type { Workflow } from '../domain/workflow';
import { useSetBoardWorkflows } from './useSaga';
import { useWorkflows } from './useWorkflows';

const ROW_BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-transparent niuu:px-2 niuu:py-1 niuu:text-[12px] niuu:text-text-muted niuu:hover:text-text-primary niuu:disabled:opacity-50';

function saveError(error: Error): string {
  return error instanceof ApiClientError && typeof error.detail === 'string' && error.detail
    ? error.detail
    : error.message;
}

/** A coordinator can only start system workflows, and only a board workflow has columns. */
function canRunOnBoard(workflow: Workflow): boolean {
  return workflow.scope === 'system' && Boolean(workflow.graph?.board);
}

function BoardWorkflowsModal({ saga, onClose }: { saga: Saga; onClose: () => void }) {
  const { data: workflows, isLoading, isError } = useWorkflows();
  const save = useSetBoardWorkflows(saga.id);
  const [chosen, setChosen] = useState<SagaBoardWorkflow[]>(saga.boardWorkflows ?? []);
  const available = (workflows ?? []).filter(
    (workflow) => canRunOnBoard(workflow) && !chosen.some((item) => item.id === workflow.id),
  );

  function move(index: number, by: number) {
    setChosen((current) => {
      const next = [...current];
      const [item] = next.splice(index, 1);
      next.splice(index + by, 0, item!);
      return next;
    });
  }

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Board workflows"
      description={`Choose the workflows ${saga.name} runs, in order. Their columns make up the board.`}
      actions={[
        { label: 'Cancel', variant: 'secondary' },
        {
          label: save.isPending ? 'Saving…' : 'Save',
          variant: 'primary',
          closes: false,
          disabled: save.isPending,
          onClick: () =>
            save.mutate(
              chosen.map((item) => item.id),
              { onSuccess: onClose },
            ),
        },
      ]}
    >
      {chosen.length > 0 ? (
        <ol className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-1 niuu:p-0">
          {chosen.map((item, index) => (
            <li
              key={item.id}
              className="niuu:flex niuu:items-center niuu:gap-2 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-2"
            >
              <span className="niuu:flex-1 niuu:text-sm niuu:text-text-primary">{item.name}</span>
              <button
                type="button"
                className={ROW_BUTTON}
                aria-label={`Move ${item.name} up`}
                disabled={index === 0}
                onClick={() => move(index, -1)}
              >
                Up
              </button>
              <button
                type="button"
                className={ROW_BUTTON}
                aria-label={`Move ${item.name} down`}
                disabled={index === chosen.length - 1}
                onClick={() => move(index, 1)}
              >
                Down
              </button>
              <button
                type="button"
                className={ROW_BUTTON}
                aria-label={`Remove ${item.name}`}
                onClick={() =>
                  setChosen((current) => current.filter((entry) => entry.id !== item.id))
                }
              >
                Remove
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <p className="niuu:m-0 niuu:text-sm niuu:text-text-muted">
          With none chosen, the board runs the project workflow.
        </p>
      )}
      <div className="niuu:mt-4 niuu:flex niuu:flex-col niuu:gap-1">
        {isLoading ? <p className="niuu:text-sm niuu:text-text-muted">Loading workflows…</p> : null}
        {isError ? (
          <p className="niuu:text-sm niuu:text-text-muted">Failed to load workflows.</p>
        ) : null}
        {available.map((workflow) => (
          <button
            key={workflow.id}
            type="button"
            onClick={() =>
              setChosen((current) => [...current, { id: workflow.id, name: workflow.name }])
            }
            className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-transparent niuu:px-3 niuu:py-2 niuu:text-left niuu:text-sm niuu:text-text-secondary niuu:hover:text-text-primary"
          >
            Add {workflow.name}
          </button>
        ))}
      </div>
      {save.error ? (
        <p className="niuu:mt-3 niuu:text-sm niuu:text-critical-fg" role="alert">
          {saveError(save.error)}
        </p>
      ) : null}
    </Modal>
  );
}

export function BoardWorkflowsCard({ saga }: { saga: Saga }) {
  const [open, setOpen] = useState(false);
  const chosen = saga.boardWorkflows ?? [];
  return (
    <section
      aria-label="Board workflows"
      className="niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:overflow-hidden"
    >
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:px-5 niuu:py-4 niuu:border-b niuu:border-border-subtle">
        <h3 className="niuu:m-0 niuu:text-[17px] niuu:font-semibold niuu:text-text-primary">
          Board workflows
        </h3>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-3 niuu:py-1.5 niuu:text-[12px] niuu:font-mono niuu:text-text-secondary niuu:hover:text-text-primary"
        >
          Choose
        </button>
      </div>
      <div className="niuu:p-5">
        {chosen.length > 0 ? (
          <ol className="niuu:m-0 niuu:flex niuu:flex-col niuu:gap-1 niuu:pl-5 niuu:text-[15px] niuu:text-text-primary">
            {chosen.map((item) => (
              <li key={item.id}>{item.name}</li>
            ))}
          </ol>
        ) : (
          <p className="niuu:m-0 niuu:text-[13px] niuu:leading-6 niuu:text-text-secondary">
            The board runs the project workflow. Choose workflows here to run several in order.
          </p>
        )}
      </div>
      {open ? <BoardWorkflowsModal saga={saga} onClose={() => setOpen(false)} /> : null}
    </section>
  );
}
