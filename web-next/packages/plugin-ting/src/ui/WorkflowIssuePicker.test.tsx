import { render, screen, waitFor } from '@testing-library/react';
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

  it('points at settings when no board comes back', async () => {
    renderPicker({ listProjects: vi.fn().mockResolvedValue([]), listIssues: vi.fn() });
    expect(await screen.findByText('No boards')).toBeInTheDocument();
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
    await waitFor(() => expect(screen.getByText('No issues on this board')).toBeInTheDocument());
  });
});
