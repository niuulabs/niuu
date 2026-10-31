import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { IWorkService } from '../../ports';
import type { ReviewItem } from '../../domain/review';
import { ReviewsPage } from './ReviewsPage';

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    to,
    className,
  }: {
    children: React.ReactNode;
    to: string;
    className?: string;
  }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
}));

function item(overrides: Partial<ReviewItem>): ReviewItem {
  return {
    taskId: 'a2a-1',
    kind: 'ticket',
    title: 'Plan the ticket',
    workflowName: 'Ticket Planning Flow',
    state: 'waiting',
    gateLabel: 'Plan review',
    updatedAt: '2026-10-31T10:00:00Z',
    projectId: 'project-1',
    projectName: 'Platform',
    issueId: 'issue-1',
    identifier: 'PLT-212',
    ...overrides,
  };
}

function setup(reviews: IWorkService['reviews']) {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ServicesProvider services={{ 'ting.work': { reviews } as unknown as IWorkService }}>
        <ReviewsPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('ReviewsPage', () => {
  it('groups reviews and links each to its review', async () => {
    setup(
      vi.fn().mockResolvedValue({
        items: [
          item({}),
          item({
            taskId: 'plan-1',
            kind: 'projectPlan',
            title: 'Observability',
            state: 'done',
            gateLabel: '',
            identifier: null,
            issueId: null,
            projectId: null,
            projectName: null,
          }),
          item({ taskId: 'a2a-2', state: 'running', title: 'Building', gateLabel: '' }),
          item({ taskId: 'a2a-3', state: 'failed', title: 'Broken', gateLabel: '' }),
        ],
      }),
    );

    const waiting = await screen.findByRole('region', { name: 'Waiting for you' });
    expect(within(waiting).getByRole('link')).toHaveAttribute(
      'href',
      '/ting/review/project-1/issue-1',
    );
    expect(within(waiting).getByRole('link')).toHaveTextContent('PLT-212 · Plan the ticket');
    const decided = screen.getByRole('region', { name: 'Decided' });
    expect(within(decided).getByRole('link')).toHaveAttribute('href', '/ting/plan/plan-1');
    expect(within(decided).getByRole('link')).toHaveTextContent('Project plan');
    expect(screen.getByRole('region', { name: 'In progress' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Stopped' })).toBeInTheDocument();
  });

  it('keeps the way to plan a project', async () => {
    setup(vi.fn().mockResolvedValue({ items: [] }));

    expect(await screen.findByText('Nothing to review yet.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Plan a project' })).toHaveAttribute(
      'href',
      '/ting/plan/new',
    );
  });

  it('says when reviews cannot be read', async () => {
    setup(vi.fn().mockRejectedValue(new Error('Ting is down')));

    expect(await screen.findByRole('alert')).toHaveTextContent('Ting is down');
  });
});
