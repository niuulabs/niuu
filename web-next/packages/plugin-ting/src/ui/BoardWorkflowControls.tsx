import { useState } from 'react';
import { Link } from '@tanstack/react-router';
import { ApiClientError } from '@niuulabs/query';
import type { Saga } from '../domain/saga';
import type { WorkBoardLayout } from '../domain/work';
import type { Workflow } from '../domain/workflow';
import { boardWorkflows } from '../domain/workBoard';
import { useAssignSagaWorkflow, useSetBoardWorkflows } from './useSaga';
import { useWorkflows } from './useWorkflows';

interface BoardColumnShape {
  title?: string;
  gate?: string;
}

/** A coordinator can only start system workflows, and only a board workflow has columns. */
export function canRunOnBoard(workflow: Workflow): boolean {
  return workflow.scope === 'system' && Boolean(workflow.graph?.board);
}

/** The columns a workflow puts on a board, as its graph declares them. */
export function workflowBoardColumns(workflow: Workflow): { title: string; gate: boolean }[] {
  const board = workflow.graph?.board as { columns?: BoardColumnShape[] } | undefined;
  return (board?.columns ?? []).map((column) => ({
    title: column.title ?? '',
    gate: Boolean(column.gate),
  }));
}

/** A workflow changes code unless its graph says otherwise, as Ting's repository check reads it. */
function needsRepository(workflow: Workflow): boolean {
  return workflow.graph?.requiresRepository !== false;
}

function saveError(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiClientError && typeof error.detail === 'string' && error.detail) {
    return error.detail;
  }
  return error instanceof Error ? error.message : String(error);
}

export interface BoardWorkflowEdits {
  saga: Saga;
  ids: readonly string[];
  saving: boolean;
  error: string | null;
  add: (workflowId: string) => void;
  remove: (workflowId: string) => void;
  moveEarlier: (workflowId: string) => void;
}

/**
 * The project's board workflows, edited where a person sees them. Every change saves at once;
 * the order on the board is the order tickets run them in.
 */
export function useBoardWorkflowEdits(
  saga: Saga,
  board: WorkBoardLayout | null,
): BoardWorkflowEdits {
  const save = useSetBoardWorkflows(saga.id);
  const assign = useAssignSagaWorkflow(saga.id);
  const ids = board ? boardWorkflows(board).map(({ id }) => id) : [];

  function persist(next: string[]) {
    save.mutate(next, {
      // With no board workflows the old single project workflow would run again: clear it too.
      onSuccess: () => {
        if (next.length === 0 && saga.workflowId) assign.mutate(null);
      },
    });
  }

  return {
    saga,
    ids,
    saving: save.isPending || assign.isPending,
    error: saveError(save.error ?? assign.error),
    add: (workflowId) => persist([...ids, workflowId]),
    remove: (workflowId) => persist(ids.filter((id) => id !== workflowId)),
    moveEarlier: (workflowId) => {
      const index = ids.indexOf(workflowId);
      if (index <= 0) return;
      const next = [...ids];
      next.splice(index, 1);
      next.splice(index - 1, 0, workflowId);
      persist(next);
    },
  };
}

/** The menu on a workflow's band: open it, run it earlier, or take it off the board. */
export function BoardBandMenu({
  workflowId,
  name,
  edits,
}: {
  workflowId: string;
  name: string;
  edits: BoardWorkflowEdits;
}) {
  const [open, setOpen] = useState(false);
  const index = edits.ids.indexOf(workflowId);
  return (
    <div className="ting-board-band-menu">
      <button
        type="button"
        className="ting-board-band-menu__toggle"
        aria-label={`${name} options`}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        ···
      </button>
      {open ? (
        <div role="menu" aria-label={`${name} options`} className="ting-board-band-menu__list">
          <Link
            role="menuitem"
            to={'/ting/workflows' as never}
            search={{ workflow: workflowId } as never}
          >
            Open workflow
          </Link>
          {index > 0 ? (
            <button
              type="button"
              role="menuitem"
              disabled={edits.saving}
              onClick={() => {
                setOpen(false);
                edits.moveEarlier(workflowId);
              }}
            >
              Run it earlier
            </button>
          ) : null}
          <button
            type="button"
            role="menuitem"
            className="ting-board-band-menu__remove"
            disabled={edits.saving}
            onClick={() => {
              setOpen(false);
              edits.remove(workflowId);
            }}
          >
            Take off the board
          </button>
          <span>Tickets already running it finish it first.</span>
        </div>
      ) : null}
    </div>
  );
}

/** The last column of the board: where a person adds the next workflow. */
export function AddWorkflowColumn({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      className="ting-board-add"
      aria-expanded={open}
      data-testid="board-add-workflow"
      onClick={onToggle}
    >
      <span aria-hidden="true">+</span>
      <strong>Add a workflow</strong>
      <small>It runs after the last one</small>
    </button>
  );
}

/** The workflows that can go on this board, each with the columns it adds. */
export function AddWorkflowPanel({
  edits,
  onClose,
}: {
  edits: BoardWorkflowEdits;
  onClose: () => void;
}) {
  const { data: workflows, isLoading, error } = useWorkflows();
  const onBoard = new Set(edits.ids);
  const choices = (workflows ?? []).filter(canRunOnBoard);
  const hasRepository = edits.saga.repos.length > 0;
  return (
    <aside className="ting-board-panel" aria-label="Add a workflow">
      <div className="ting-board-panel__head">
        <span className="ting-work-eyebrow">Board</span>
        <button type="button" onClick={onClose}>
          Hide
        </button>
      </div>
      <h3>Add a workflow</h3>
      <p className="ting-board-panel__text">Its columns join the board after the last workflow.</p>
      {isLoading ? <p className="ting-board-panel__text">Loading workflows…</p> : null}
      {error ? (
        <p role="alert" className="ting-board-panel__text">
          Workflows could not be read: {error.message}
        </p>
      ) : null}
      <ul className="ting-board-choices">
        {choices.map((workflow) => {
          const blocked = needsRepository(workflow) && !hasRepository;
          return (
            <li key={workflow.id} className="ting-board-choice">
              <strong>{workflow.name}</strong>
              <span className="ting-board-choice__columns">
                {workflowBoardColumns(workflow).map((column) => (
                  <span
                    key={column.title}
                    className={
                      column.gate ? 'ting-board-chip ting-board-chip--gate' : 'ting-board-chip'
                    }
                  >
                    {column.title}
                  </span>
                ))}
              </span>
              {onBoard.has(workflow.id) ? (
                <small>On this board</small>
              ) : blocked ? (
                <small className="ting-board-choice__blocked">
                  Changes code, so it needs a repository on this project.
                </small>
              ) : (
                <button
                  type="button"
                  disabled={edits.saving}
                  onClick={() => {
                    edits.add(workflow.id);
                    onClose();
                  }}
                >
                  Add
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {edits.error ? (
        <p role="alert" className="ting-board-panel__text ting-board-choice__blocked">
          {edits.error}
        </p>
      ) : null}
      <p className="ting-board-panel__text">
        Only workflows with board columns are listed. Give any other workflow columns in the builder
        and it shows up here.
      </p>
    </aside>
  );
}
