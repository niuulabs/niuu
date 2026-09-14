/**
 * WorkflowIssuePicker — pick a tracker issue to start a workflow from.
 *
 * Board (`listProjects`) on the left, its issues (`listIssues`) on the right.
 * Picking one hands the caller the issue; the caller decides what to do with
 * it (the workflows page drops it into the launch prompt).
 *
 * Owner: plugin-ting.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { EmptyState, LoadingState, Modal, cn } from '@niuulabs/ui';
import type { ITrackerBrowserService, TrackerIssue } from '../ports';

export interface WorkflowIssuePickerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (issue: TrackerIssue) => void;
}

/** The prompt text an issue seeds: identifier, title, then the link. */
export function issueLaunchPrompt(issue: TrackerIssue): string {
  return `${issue.identifier} · ${issue.title}\n${issue.url}`;
}

export function WorkflowIssuePicker({ open, onOpenChange, onPick }: WorkflowIssuePickerProps) {
  const tracker = useService<ITrackerBrowserService>('ting.tracker');
  const [selectedBoardId, setSelectedBoardId] = useState<string | null>(null);

  const boards = useQuery({
    queryKey: ['ting', 'tracker', 'projects'],
    queryFn: () => tracker.listProjects(),
    enabled: open,
  });

  const boardId = selectedBoardId ?? boards.data?.[0]?.id ?? null;

  const issues = useQuery({
    queryKey: ['ting', 'tracker', 'issues', boardId],
    queryFn: () => tracker.listIssues(boardId as string),
    enabled: open && !!boardId,
  });

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Start from a Linear issue"
      description="Pick the issue this run is about. Its identifier, title, and link become the prompt."
      actions={[{ label: 'Cancel', variant: 'secondary' }]}
    >
      <div
        data-testid="workflow-issue-picker"
        className="niuu:mt-4 niuu:grid niuu:grid-cols-[200px_1fr] niuu:gap-4"
      >
        <div className="niuu:flex niuu:flex-col niuu:gap-1 niuu:max-h-[320px] niuu:overflow-y-auto">
          <span className="niuu:text-[10px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.24em] niuu:text-text-muted">
            Boards
          </span>
          {boards.isLoading ? <LoadingState label="Loading boards…" /> : null}
          {boards.isError ? (
            <p className="niuu:m-0 niuu:text-[12px] niuu:text-critical" role="alert">
              {boards.error instanceof Error ? boards.error.message : 'Failed to load boards.'}
            </p>
          ) : null}
          {boards.data?.map((board) => (
            <button
              key={board.id}
              type="button"
              data-testid={`workflow-issue-board-${board.id}`}
              onClick={() => setSelectedBoardId(board.id)}
              className={cn(
                'niuu:rounded-md niuu:px-2.5 niuu:py-1.5 niuu:text-left niuu:text-[12px] niuu:cursor-pointer niuu:border',
                board.id === boardId
                  ? 'niuu:border-border niuu:bg-bg-elevated niuu:text-text-primary'
                  : 'niuu:border-transparent niuu:bg-transparent niuu:text-text-secondary niuu:hover:bg-bg-tertiary',
              )}
            >
              {board.name}
            </button>
          ))}
          {!boards.isLoading && !boards.isError && (boards.data?.length ?? 0) === 0 ? (
            <EmptyState
              title="No boards"
              description="Connect a tracker in Ting settings, then try again."
            />
          ) : null}
        </div>

        <div className="niuu:flex niuu:flex-col niuu:gap-1 niuu:max-h-[320px] niuu:overflow-y-auto">
          <span className="niuu:text-[10px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.24em] niuu:text-text-muted">
            Issues
          </span>
          {issues.isLoading && boardId ? <LoadingState label="Loading issues…" /> : null}
          {issues.isError ? (
            <p className="niuu:m-0 niuu:text-[12px] niuu:text-critical" role="alert">
              {issues.error instanceof Error ? issues.error.message : 'Failed to load issues.'}
            </p>
          ) : null}
          {issues.data?.map((issue) => (
            <button
              key={issue.id}
              type="button"
              data-testid={`workflow-issue-${issue.id}`}
              onClick={() => {
                onPick(issue);
                onOpenChange(false);
              }}
              className="niuu:flex niuu:items-baseline niuu:gap-2 niuu:rounded-md niuu:border niuu:border-transparent niuu:px-2.5 niuu:py-1.5 niuu:text-left niuu:cursor-pointer niuu:hover:border-border niuu:hover:bg-bg-tertiary"
            >
              <span className="niuu:font-mono niuu:text-[10px] niuu:text-text-faint niuu:shrink-0">
                {issue.identifier}
              </span>
              <span className="niuu:truncate niuu:text-[12px] niuu:text-text-primary">
                {issue.title}
              </span>
              <span className="niuu:ml-auto niuu:font-mono niuu:text-[10px] niuu:text-text-faint niuu:shrink-0">
                {issue.status}
              </span>
            </button>
          ))}
          {!issues.isLoading && !issues.isError && boardId && (issues.data?.length ?? 0) === 0 ? (
            <EmptyState title="No issues on this board" />
          ) : null}
        </div>
      </div>
    </Modal>
  );
}
