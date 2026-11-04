import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { IWorkService } from '../../ports';
import type { ReviewRevision, RunReviews } from '../../domain/review';
import { RunReviewPage } from './RunReviewPage';

vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ slug: 'a2a-9' }),
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

function revision(overrides: Partial<ReviewRevision> = {}): ReviewRevision {
  return {
    number: 1,
    gateId: 'gate-1',
    activationId: 'activation-1',
    requestedAt: '2026-10-31T10:00:00Z',
    status: 'pending',
    summary: 'Ready to merge.',
    decidedBy: '',
    notes: '',
    decidedAt: '',
    fields: {},
    ...overrides,
  };
}

function record(overrides: Partial<RunReviews> = {}): RunReviews {
  return {
    run: {
      slug: 'a2a-9',
      title: 'Plan NIU-1125 only',
      workflowName: 'Infra Change Flow',
      prompt: 'Plan NIU-1125 only\nRead the complete ticket.',
      sessionId: 'session-9',
      repo: 'https://github.com/niuulabs/niuu',
      branch: 'regin',
      startedBy: 'regin',
    },
    reviews: [
      {
        columnId: 'merge-gate',
        title: 'Approve the merge',
        review: null,
        workflowName: 'Infra Change Flow',
        taskId: 'a2a-9',
        sessionId: 'session-9',
        panels: [{ widget: 'summary', title: 'Summary' }],
        revisions: [revision()],
      },
    ],
    unreadableRuns: [],
    ...overrides,
  };
}

function setup(runReviews: IWorkService['runReviews'], decideRunGate = vi.fn()) {
  const service = { runReviews, decideRunGate } as unknown as IWorkService;
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ServicesProvider services={{ 'ting.work': service }}>
        <RunReviewPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { decideRunGate };
}

describe('RunReviewPage', () => {
  it('introduces the run and what started it', async () => {
    setup(vi.fn().mockResolvedValue(record()));

    expect(await screen.findByRole('heading', { name: 'Plan NIU-1125 only' })).toBeInTheDocument();
    expect(screen.getByText('Started by regin')).toBeInTheDocument();
    expect(screen.getByText('On regin')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the session' })).toHaveAttribute(
      'href',
      '/volundr/sessions/session-9',
    );
    expect(screen.getByText('Ready to merge.')).toBeInTheDocument();
  });

  it('answers the pending gate with what the person saw', async () => {
    const { decideRunGate } = setup(
      vi.fn().mockResolvedValue(record()),
      vi.fn().mockResolvedValue(undefined),
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }));

    await waitFor(() =>
      expect(decideRunGate).toHaveBeenCalledWith('a2a-9', {
        gateId: 'gate-1',
        activationId: 'activation-1',
        decision: 'approve',
        notes: '',
      }),
    );
  });

  it('asks for what should change, and says where it goes', async () => {
    const { decideRunGate } = setup(
      vi.fn().mockResolvedValue(record()),
      vi.fn().mockResolvedValue(undefined),
    );

    expect(await screen.findByText(/Changes go back to the agent/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Pin the version.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Request changes' }));

    await waitFor(() =>
      expect(decideRunGate).toHaveBeenCalledWith(
        'a2a-9',
        expect.objectContaining({ decision: 'request_changes', notes: 'Pin the version.' }),
      ),
    );
  });

  it('says why a decision did not go through', async () => {
    setup(
      vi.fn().mockResolvedValue(record()),
      vi.fn().mockRejectedValue(new Error('Review it again.')),
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }));

    expect(await screen.findByText('Review it again.')).toBeInTheDocument();
  });

  it('offers no decision once the gate is answered', async () => {
    const decided = record();
    decided.reviews[0]!.revisions = [revision({ status: 'approved', decidedBy: 'jozef' })];
    setup(vi.fn().mockResolvedValue(decided));

    expect(await screen.findByText('by jozef')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
  });

  it('says when the session is gone', async () => {
    setup(
      vi.fn().mockResolvedValue(record({ reviews: [], unreadableRuns: ['Infra Change Flow'] })),
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The session of Infra Change Flow is gone',
    );
    expect(screen.getByRole('link', { name: 'Back to Reviews' })).toHaveAttribute(
      'href',
      '/ting/plan',
    );
  });

  it('says when the run cannot be read', async () => {
    setup(vi.fn().mockRejectedValue(new Error('Run not found: a2a-9')));

    expect(await screen.findByRole('alert')).toHaveTextContent('Run not found: a2a-9');
  });
});
