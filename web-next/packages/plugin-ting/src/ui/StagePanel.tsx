import { Link } from '@tanstack/react-router';
import type { WorkBoardColumn, WorkBoardLayout } from '../domain/work';
import { boardWorkflows } from '../domain/workBoard';

const REVIEWED: Record<NonNullable<WorkBoardColumn['review']>, string> = {
  plan: 'The plan',
  code: 'Code and pull request',
  output: 'Workflow output',
};

function title(board: WorkBoardLayout, columnId: string | null): string {
  return board.columns.find((column) => column.id === columnId)?.title ?? 'Unknown';
}

/** The board only shows what its workflow defines; changing it means changing the workflow. */
export function StagePanel({
  board,
  workflowId,
  onClose,
}: {
  board: WorkBoardLayout;
  workflowId: string;
  onClose: () => void;
}) {
  const workflows = boardWorkflows(board);
  const position = workflows.findIndex(({ id }) => id === workflowId);
  const workflow = workflows[position];
  if (!workflow) return null;
  const next = workflows[position + 1]?.columns[0]?.title ?? 'Done';
  const gates = workflow.columns.filter((column) => column.kind === 'gate');
  const route = [...workflow.columns.map((column) => column.title), next].join(' → ');
  return (
    <aside className="ting-board-panel" aria-label={workflow.name}>
      <div className="ting-board-panel__head">
        <span className="ting-work-eyebrow">Workflow</span>
        <button type="button" onClick={onClose}>
          Hide
        </button>
      </div>
      <h3>{workflow.name}</h3>
      <p className="ting-board-panel__text">{route}</p>
      {gates.map((gate) => (
        <dl key={gate.id} className="ting-board-facts">
          <dt>Decision</dt>
          <dd>{gate.title}</dd>
          <dt>Who decides</dt>
          <dd>A person approves</dd>
          {gate.review ? (
            <>
              <dt>Reviewed</dt>
              <dd>{REVIEWED[gate.review]}</dd>
            </>
          ) : null}
          <dt>When it passes</dt>
          <dd>{title(board, gate.approveColumnId)}</dd>
          <dt>When changes are requested</dt>
          <dd>{title(board, gate.changesColumnId)}, same run</dd>
        </dl>
      ))}
      <p className="ting-board-panel__text">
        The board only shows this. To change it, open the workflow.
      </p>
      {workflow.id ? (
        <div className="ting-board-panel__links">
          <Link to={'/ting/workflows/build' as never} search={{ id: workflow.id } as never}>
            Open workflow
          </Link>
        </div>
      ) : null}
    </aside>
  );
}
