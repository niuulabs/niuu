import { useState, type ReactNode } from 'react';
import { ExternalLink } from 'lucide-react';
import { ErrorState, LoadingState } from '@niuulabs/ui';
import type { Saga } from '../domain/saga';
import type { WorkBoardLayout, WorkTask } from '../domain/work';
import {
  AddWorkflowColumn,
  AddWorkflowPanel,
  useBoardWorkflowEdits,
} from './BoardWorkflowControls';
import { ProjectBoard } from './ProjectBoard';
import { useSagas } from './useSagas';
import { useWorkDetail } from './useWork';
import { WorkCoverageNotices } from './WorkCoverageNotices';

/** The Work project behind one tracker project, and its live detail. */
export function useTrackerBoard(trackerId: string | null) {
  const sagas = useSagas();
  const saga = sagas.data?.find((entry) => entry.trackerId === trackerId) ?? null;
  const detail = useWorkDetail(saga ? 'project' : null, saga?.id ?? null);
  return {
    saga,
    detail: detail.data ?? null,
    isLoading: sagas.isLoading || detail.isLoading,
    error: sagas.error ?? detail.error ?? null,
    refetch: detail.refetch,
  };
}

/** A board with no workflows yet: its tickets wait in Backlog until one is added. */
function UnstartedBoard({ saga, tasks }: { saga: Saga; tasks: WorkTask[] }) {
  const edits = useBoardWorkflowEdits(saga, null);
  const [adding, setAdding] = useState(false);
  return (
    <div
      className={`ting-project-board${adding ? ' ting-project-board--panel' : ''}`}
      data-testid="project-board"
    >
      <div className="ting-project-board__main">
        <p className="ting-board-notice" role="status">
          No workflows on this board yet. Add one and its columns appear here; tickets start from
          Backlog.
        </p>
        <div className="ting-board-scroller">
          <section className="ting-work-board__column" aria-label="Backlog">
            <header>
              <h3>Backlog</h3>
              <span>{tasks.length}</span>
            </header>
            <div className="ting-work-board__cards">
              {tasks.map((task) => (
                <article
                  key={task.source.issueId}
                  className="ting-work-card ting-work-card--ticket"
                >
                  <a
                    href={task.source.url}
                    target="_blank"
                    rel="noreferrer"
                    className="ting-work-task__key"
                  >
                    {task.source.identifier}
                    <ExternalLink aria-hidden="true" />
                  </a>
                  <span className="ting-board-card__title">{task.title}</span>
                </article>
              ))}
            </div>
          </section>
          <AddWorkflowColumn open={adding} onToggle={() => setAdding((open) => !open)} />
        </div>
      </div>
      {adding ? <AddWorkflowPanel edits={edits} onClose={() => setAdding(false)} /> : null}
    </div>
  );
}

function EditableBoard({
  saga,
  board,
  tasks,
}: {
  saga: Saga;
  board: WorkBoardLayout;
  tasks: WorkTask[];
}) {
  const edits = useBoardWorkflowEdits(saga, board);
  return <ProjectBoard board={board} projectId={saga.id} tasks={tasks} edits={edits} />;
}

/**
 * The board of one tracker project where the project is managed: a workspace. Its workflows
 * are added, reordered and removed on the board itself.
 */
export function TrackerBoard({
  trackerId,
  fallback,
}: {
  trackerId: string;
  /** Shown when no Work project is connected to this tracker project. */
  fallback: ReactNode;
}) {
  const { saga, detail, isLoading, error, refetch } = useTrackerBoard(trackerId);
  if (isLoading) return <LoadingState label="Loading board…" />;
  if (error) return <ErrorState title="Could not load the board" message={String(error)} />;
  if (!saga || !detail) return <>{fallback}</>;
  return (
    <div className="ting-work-detail ting-work-detail--board" data-testid="tracker-board">
      <WorkCoverageNotices coverage={detail.coverage} onRetry={() => void refetch()} />
      {detail.board ? (
        <EditableBoard saga={saga} board={detail.board} tasks={detail.tasks} />
      ) : (
        <UnstartedBoard saga={saga} tasks={detail.tasks} />
      )}
    </div>
  );
}
