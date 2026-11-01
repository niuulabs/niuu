import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import type { ExtractedStructure, ITingService, PlanSession } from '../ports';
import { createMockTingService } from '../adapters/mock';
import { PlanDetailPage } from './PlanDetailPage';

const mockNavigate = vi.fn();
let mockSlug = 'plan-niu-1104-define-ravnclaw-as-niuu-s-advanced';

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useParams: () => ({ slug: mockSlug }),
}));

const plan: PlanSession = {
  sessionId: '0d614987-101e-45e8-acb1-778f41d36b93',
  campaignSlug: 'plan-niu-1104-define-ravnclaw-as-niuu-s-advanced',
  name: 'NIU-1104 RavnClaw runtime specialization',
  prompt: 'Define RavnClaw as a Ravn-layer native resident runtime specialization.',
  repo: 'niuulabs/niuu',
  status: 'completed',
  workflowName: 'Saga Planning',
  chatEndpoint: null,
};

const draft: ExtractedStructure = {
  found: true,
  structure: {
    name: 'NIU-1104 RavnClaw',
    risks: [{ kind: 'scope', message: 'Keep to a reviewable spec before runtime work.' }],
    phases: [
      {
        name: 'Approved plan',
        runs: [
          {
            name: 'Author the RavnClaw boundary spec',
            description: 'Author docs/operator/ravnclaw-runtime.md.',
            acceptanceCriteria: ['Spec defines the Ravn-layer boundary'],
            declaredFiles: ['docs/operator/ravnclaw-runtime.md'],
            estimateHours: 3,
            confidence: 80,
          },
          {
            name: 'Map interfaces and integrations',
            description: 'Preserve the Ravn/Niuu/Skuld boundaries.',
            acceptanceCriteria: [],
            declaredFiles: [],
            estimateHours: 2,
            confidence: 75,
          },
        ],
      },
    ],
  },
};

function wrap(svc: Partial<ITingService>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ting = { ...createMockTingService(), ...svc } as ITingService;
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ ting }}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('PlanDetailPage', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockSlug = 'plan-niu-1104-define-ravnclaw-as-niuu-s-advanced';
  });

  it('shows the plan and its run breakdown together', async () => {
    const svc = {
      getPlanSession: vi.fn().mockResolvedValue(plan),
      getPlanDraft: vi.fn().mockResolvedValue(draft),
    };
    render(<PlanDetailPage />, { wrapper: wrap(svc) });

    await waitFor(() =>
      expect(screen.getByText('NIU-1104 RavnClaw runtime specialization')).toBeInTheDocument(),
    );
    // the plan
    expect(screen.getByText(/Define RavnClaw as a Ravn-layer/)).toBeInTheDocument();
    expect(screen.getByText('Saga Planning')).toBeInTheDocument();
    // the breakdown
    expect(screen.getByText('Author the RavnClaw boundary spec')).toBeInTheDocument();
    expect(screen.getByText('Map interfaces and integrations')).toBeInTheDocument();
    expect(screen.getByText('Spec defines the Ravn-layer boundary')).toBeInTheDocument();
  });

  it('surfaces the risks the planner flagged', async () => {
    const svc = {
      getPlanSession: vi.fn().mockResolvedValue(plan),
      getPlanDraft: vi.fn().mockResolvedValue(draft),
    };
    render(<PlanDetailPage />, { wrapper: wrap(svc) });

    await waitFor(() => expect(screen.getByText('Known risks')).toBeInTheDocument());
    expect(screen.getByText('Keep to a reviewable spec before runtime work.')).toBeInTheDocument();
  });

  it('renders the plan when it has no decomposed runs', async () => {
    const svc = {
      getPlanSession: vi.fn().mockResolvedValue(plan),
      getPlanDraft: vi.fn().mockResolvedValue({ found: false, structure: null }),
    };
    render(<PlanDetailPage />, { wrapper: wrap(svc) });

    await waitFor(() => expect(screen.getByText(/has no decomposed runs/)).toBeInTheDocument());
    expect(screen.getByText('NIU-1104 RavnClaw runtime specialization')).toBeInTheDocument();
  });

  it('reports a plan that does not exist rather than rendering an empty shell', async () => {
    const svc = {
      getPlanSession: vi.fn().mockResolvedValue(null),
      getPlanDraft: vi.fn().mockResolvedValue(draft),
    };
    render(<PlanDetailPage />, { wrapper: wrap(svc) });

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByText('Plan not found.')).toBeInTheDocument();
  });

  it('goes back to the plan list', async () => {
    const svc = {
      getPlanSession: vi.fn().mockResolvedValue(plan),
      getPlanDraft: vi.fn().mockResolvedValue(draft),
    };
    render(<PlanDetailPage />, { wrapper: wrap(svc) });

    await waitFor(() => expect(screen.getByRole('button', { name: /Plans/ })).toBeInTheDocument());
    screen.getByRole('button', { name: /Plans/ }).click();
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/plan' });
  });
});
