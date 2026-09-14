import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ResearchCampaign } from '../ports';
import { WorkflowRunsCard } from './WorkflowRunsCard';

const mockOpenEventStream = vi.hoisted(() => vi.fn());

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    children,
    params: _params,
    ...rest
  }: {
    to: string;
    children: ReactNode;
    params?: unknown;
  }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock('@niuulabs/query', () => ({
  openEventStream: mockOpenEventStream,
}));

const WORKFLOW_ID = '11111111-1111-4111-8111-111111111111';
const now = '2026-09-01T10:00:00.000Z';

function campaign(overrides: Partial<ResearchCampaign> = {}): ResearchCampaign {
  return {
    id: '33333333-3333-4333-8333-333333333333',
    slug: 'a-run',
    name: 'A run',
    ownerId: 'jozef',
    workflowId: WORKFLOW_ID,
    workflowVersion: '1.0.0',
    workflowName: 'Build and ship',
    sessionId: 'session-1',
    sessionName: 'A run',
    status: 'running',
    activeStageId: null,
    stageState: [],
    metadata: {},
    createdAt: now,
    updatedAt: now,
    ...overrides,
  };
}

function renderCard(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={services}>
        <WorkflowRunsCard workflowId={WORKFLOW_ID} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('WorkflowRunsCard', () => {
  beforeEach(() => {
    mockOpenEventStream.mockReset();
    mockOpenEventStream.mockReturnValue({ close: vi.fn() });
  });

  it('shows a loading state first', () => {
    renderCard({
      'ting.specs': { listCampaigns: vi.fn().mockReturnValue(new Promise(() => undefined)) },
      'ting.research': { listCampaigns: vi.fn().mockResolvedValue([]) },
    });
    expect(screen.getByText('Loading runs…')).toBeInTheDocument();
  });

  it('surfaces a load failure', async () => {
    renderCard({
      'ting.specs': { listCampaigns: vi.fn().mockRejectedValue(new Error('ting is down')) },
      'ting.research': { listCampaigns: vi.fn().mockResolvedValue([]) },
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('ting is down');
  });

  it('says when nothing has run yet', async () => {
    renderCard({
      'ting.specs': { listCampaigns: vi.fn().mockResolvedValue([]) },
      'ting.research': { listCampaigns: vi.fn().mockResolvedValue([]) },
    });
    expect(await screen.findByText('No runs recorded for this workflow yet.')).toBeInTheDocument();
  });

  it('describes finished, queued and in-flight runs', async () => {
    renderCard({
      'ting.specs': {
        listCampaigns: vi
          .fn()
          .mockResolvedValue([
            campaign({ id: 'a', slug: 'done', name: 'Done', status: 'completed' }),
            campaign({ id: 'b', slug: 'queued', name: 'Queued', status: 'pending' }),
          ]),
      },
      'ting.research': {
        listCampaigns: vi.fn().mockResolvedValue([
          campaign({
            id: 'c',
            slug: 'researching',
            name: 'Researching',
            stageState: [
              { stageId: 's1', label: 'Sweep', status: 'active' },
              { stageId: 's2', label: 'Write', status: 'pending' },
            ],
          }),
        ]),
      },
    });

    await waitFor(() => expect(screen.getByTestId('workflow-run-done')).toBeInTheDocument());
    expect(screen.getByTestId('workflow-run-done')).toHaveTextContent('finished');
    expect(screen.getByTestId('workflow-run-queued')).toHaveTextContent('queued');
    expect(screen.getByTestId('workflow-run-researching')).toHaveTextContent('Sweep');
    // A research campaign has no review endpoint, so it offers no decision.
    expect(screen.queryByTestId('workflow-run-approve-researching')).not.toBeInTheDocument();
  });

  it('leaves a research campaign with a pending gate undecidable', async () => {
    renderCard({
      'ting.specs': { listCampaigns: vi.fn().mockResolvedValue([]) },
      'ting.research': {
        listCampaigns: vi.fn().mockResolvedValue([
          campaign({
            slug: 'gated-research',
            metadata: {
              pending_workflow_gates: [{ id: 'g1', node_id: 'spec-prd-gate', summary: 'Review' }],
            },
          }),
        ]),
      },
    });

    await waitFor(() =>
      expect(screen.getByTestId('workflow-run-gated-research')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('workflow-run-approve-gated-research')).not.toBeInTheDocument();
  });

  it('lets the reviewer back out of requesting changes', async () => {
    renderCard({
      'ting.specs': {
        listCampaigns: vi.fn().mockResolvedValue([
          campaign({
            slug: 'gated',
            status: 'blocked',
            metadata: {
              pending_workflow_gates: [
                { id: 'g1', node_id: 'spec-prd-gate', summary: 'PRD review required' },
              ],
            },
          }),
        ]),
        reviewCampaign: vi.fn(),
      },
      'ting.research': { listCampaigns: vi.fn().mockResolvedValue([]) },
    });

    await waitFor(() =>
      expect(screen.getByTestId('workflow-run-changes-gated')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('workflow-run-changes-gated'));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByTestId('workflow-run-approve-gated')).toBeInTheDocument();
  });

  it('surfaces a review failure', async () => {
    renderCard({
      'ting.specs': {
        listCampaigns: vi.fn().mockResolvedValue([
          campaign({
            slug: 'gated',
            status: 'blocked',
            metadata: {
              pending_workflow_gates: [
                { id: 'g1', node_id: 'spec-prd-gate', summary: 'PRD review required' },
              ],
            },
          }),
        ]),
        reviewCampaign: vi.fn().mockRejectedValue(new Error('gate already resolved')),
      },
      'ting.research': { listCampaigns: vi.fn().mockResolvedValue([]) },
    });

    await waitFor(() =>
      expect(screen.getByTestId('workflow-run-approve-gated')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('workflow-run-approve-gated'));

    expect(await screen.findByRole('alert')).toHaveTextContent('gate already resolved');
  });
});
