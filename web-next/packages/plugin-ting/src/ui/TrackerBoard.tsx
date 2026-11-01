import type { ReactNode } from 'react';
import { ErrorState, LoadingState } from '@niuulabs/ui';
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

/** The Work board of one tracker project, for pages that are not the Work page. */
export function TrackerBoard({
  trackerId,
  fallback,
}: {
  trackerId: string;
  /** Shown when the tracker project has no board to draw yet. */
  fallback: ReactNode;
}) {
  const { saga, detail, isLoading, error, refetch } = useTrackerBoard(trackerId);
  if (isLoading) return <LoadingState label="Loading board…" />;
  if (error) return <ErrorState title="Could not load the board" message={String(error)} />;
  if (!saga || !detail?.board) return <>{fallback}</>;
  return (
    <div className="ting-work-detail ting-work-detail--board" data-testid="tracker-board">
      <WorkCoverageNotices coverage={detail.coverage} onRetry={() => void refetch()} />
      <ProjectBoard board={detail.board} projectId={saga.id} tasks={detail.tasks} />
    </div>
  );
}
