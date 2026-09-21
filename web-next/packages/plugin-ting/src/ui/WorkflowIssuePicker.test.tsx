import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import type { TrackerIssue } from '../ports';
import { WorkflowIssuePicker, issueLaunchPrompt } from './WorkflowIssuePicker';

const issue: TrackerIssue = {
  id: 'issue-1',
  identifier: 'NIU-1',
  title: 'Fix the gate',
  description: '',
  status: 'todo',
  assignee: null,
  labels: [],
  priority: 2,
  url: 'https://linear.app/issue/NIU-1',
  milestoneId: null,
};

function renderPicker(tracker: Record<string, unknown>, open = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ 'ting.tracker': tracker }}>
        <WorkflowIssuePicker open={open} onOpenChange={vi.fn()} onPick={vi.fn()} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return client;
}

describe('issueLaunchPrompt', () => {
  it('puts the identifier, title and link into the prompt', () => {
    expect(issueLaunchPrompt(issue)).toBe('NIU-1 · Fix the gate\nhttps://linear.app/issue/NIU-1');
  });
});

describe('WorkflowIssuePicker', () => {
  it('asks for nothing while closed', () => {
    const listProjects = vi.fn().mockResolvedValue([]);
    renderPicker({ listProjects, listIssues: vi.fn() }, false);
    expect(listProjects).not.toHaveBeenCalled();
  });

  it('surfaces a board load failure', async () => {
    renderPicker({
      listProjects: vi.fn().mockRejectedValue(new Error('tracker unreachable')),
      listIssues: vi.fn(),
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('tracker unreachable');
  });

  it('uses provider-neutral tracker language', async () => {
    renderPicker({ listProjects: vi.fn().mockResolvedValue([]), listIssues: vi.fn() });
    expect(screen.getByRole('dialog', { name: 'Use a tracker issue' })).toBeInTheDocument();
    expect(screen.queryByText(/Linear|Boards?/i)).not.toBeInTheDocument();
  });

  it('points at settings when no tracker project comes back', async () => {
    renderPicker({ listProjects: vi.fn().mockResolvedValue([]), listIssues: vi.fn() });
    expect(await screen.findByText('No tracker projects')).toBeInTheDocument();
  });

  it('surfaces an issue load failure and an empty board', async () => {
    const board = {
      id: 'board-1',
      name: 'Platform',
      description: '',
      status: 'started',
      url: '',
      milestoneCount: 0,
      issueCount: 0,
      slug: 'platform',
    };
    renderPicker({
      listProjects: vi.fn().mockResolvedValue([board]),
      listIssues: vi.fn().mockRejectedValue(new Error('issues unavailable')),
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('issues unavailable');

    renderPicker({
      listProjects: vi.fn().mockResolvedValue([board]),
      listIssues: vi.fn().mockResolvedValue([]),
    });
    await waitFor(() => expect(screen.getByText('No issues in this project')).toBeInTheDocument());
  });

  it('qualifies duplicate project ids by tracker connection in requests and query keys', async () => {
    const projects = [
      {
        id: 'shared-project',
        name: 'Platform · Tracker A',
        description: '',
        status: 'started',
        url: '',
        milestoneCount: 0,
        issueCount: 1,
        slug: 'platform-a',
        trackerConnectionId: 'tracker-a',
      },
      {
        id: 'shared-project',
        name: 'Platform · Tracker B',
        description: '',
        status: 'started',
        url: '',
        milestoneCount: 0,
        issueCount: 1,
        slug: 'platform-b',
        trackerConnectionId: 'tracker-b',
      },
    ];
    const listIssues = vi.fn().mockResolvedValue([]);
    const client = renderPicker({
      listProjects: vi.fn().mockResolvedValue(projects),
      listIssues,
    });

    await waitFor(() =>
      expect(listIssues).toHaveBeenCalledWith('shared-project', undefined, 'tracker-a'),
    );
    fireEvent.click(screen.getByTestId('workflow-issue-project-tracker-b-shared-project'));
    await waitFor(() =>
      expect(listIssues).toHaveBeenCalledWith('shared-project', undefined, 'tracker-b'),
    );

    expect(
      client.getQueryCache().find({
        queryKey: ['ting', 'tracker', 'issues', 'tracker-a', 'shared-project'],
        exact: true,
      }),
    ).toBeDefined();
    expect(
      client.getQueryCache().find({
        queryKey: ['ting', 'tracker', 'issues', 'tracker-b', 'shared-project'],
        exact: true,
      }),
    ).toBeDefined();
  });
});
