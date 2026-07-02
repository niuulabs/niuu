import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PluginCtxProvider, ServicesProvider, type PluginCtx } from '@niuulabs/plugin-sdk';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CampaignArtifactDetail, SpecCampaignDetail } from '../ports';
import type { ISpecsService } from '../ports';
import { SpecsCampaignPage } from './SpecsCampaignPage';
import { SpecsCenterPage } from './SpecsCenterPage';

const mockNavigate = vi.fn();
const mockSetTweak = vi.fn();
const mockOpenEventStream = vi.hoisted(() => vi.fn());
let mockSlug = 'sdcp-operator';

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useParams: () => ({ slug: mockSlug }),
}));

vi.mock('@niuulabs/query', () => ({
  openEventStream: mockOpenEventStream,
}));

const now = '2026-07-02T12:00:00.000Z';

const campaign: SpecCampaignDetail = {
  id: '22222222-2222-4222-8222-222222222222',
  slug: 'sdcp-operator',
  name: 'SDCP Kubernetes operator',
  ownerId: 'jozef',
  workflowId: '33333333-3333-4333-8333-333333333333',
  workflowVersion: '1.0.0',
  workflowName: 'Specification Stack',
  sessionId: 'session-sdcp',
  sessionName: 'SDCP spec run',
  status: 'blocked',
  activeStageId: 'spec-prd-gate',
  stageState: [
    {
      stageId: 'spec-brief',
      label: 'Brief',
      status: 'complete',
      startedAt: now,
      completedAt: now,
    },
    {
      stageId: 'spec-prd-gate',
      label: 'Review PRD',
      status: 'blocked',
      startedAt: now,
      completedAt: null,
    },
  ],
  metadata: {
    prompt: 'Plan SDCP-Smart-Device-Control-Protocol-V3.0.0 as a Kubernetes operator.',
    repos: ['niuulabs/sdcp-operator', 'niuulabs/printer-runtime'],
    pending_workflow_gates: [
      {
        id: 'gate-prd',
        node_id: 'spec-prd-gate',
        summary: 'PRD review required',
        instructions: 'Review the PRD before SRD work starts.',
      },
    ],
  },
  createdAt: now,
  updatedAt: now,
  lastActivityAt: now,
  completedAt: null,
  artifacts: [
    {
      path: 'specifications/sdcp-operator/10-prd.md',
      title: 'PRD',
      updatedAt: now,
      kind: 'prd',
      publishState: 'review-ready',
      sourceIds: [],
      summary: 'Product requirements',
    },
    {
      path: 'specifications/sdcp-operator/11-prd-review.md',
      title: 'PRD review',
      updatedAt: now,
      kind: 'prd_review',
      publishState: 'review-ready',
      sourceIds: [],
      summary: 'Critic notes',
    },
  ],
  canonicalArtifacts: {
    prd: 'specifications/sdcp-operator/10-prd.md',
    prd_review: 'specifications/sdcp-operator/11-prd-review.md',
  },
};

const artifacts = new Map<string, CampaignArtifactDetail>([
  [
    'specifications/sdcp-operator/10-prd.md',
    {
      ...campaign.artifacts[0]!,
      content: '# Product Requirements\n\nManage and track SDCP printers from Kubernetes.',
    },
  ],
  [
    'specifications/sdcp-operator/11-prd-review.md',
    {
      ...campaign.artifacts[1]!,
      content: '# PRD review\n\nClarify device-plugin ownership before approval.',
    },
  ],
]);

function makeSpecsService(overrides?: Partial<ISpecsService>): ISpecsService {
  return {
    async listCampaigns() {
      return [campaign];
    },
    async getCampaign(slug) {
      return slug === campaign.slug ? campaign : null;
    },
    async createCampaign() {
      return campaign;
    },
    async deleteCampaign() {},
    async listArtifacts() {
      return campaign.artifacts;
    },
    async getArtifact(_slug, path) {
      return artifacts.get(path) ?? null;
    },
    async reviewCampaign() {
      return campaign;
    },
    ...overrides,
  };
}

function wrap(services: Record<string, unknown>, pluginCtx?: PluginCtx) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ctx: PluginCtx = pluginCtx ?? { tweaks: {}, setTweak: mockSetTweak };
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>
          <PluginCtxProvider value={ctx}>{children}</PluginCtxProvider>
        </ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('Specs pages', () => {
  beforeEach(() => {
    mockNavigate.mockClear();
    mockSetTweak.mockClear();
    mockOpenEventStream.mockReset();
    mockOpenEventStream.mockReturnValue({ close: vi.fn() });
    mockSlug = 'sdcp-operator';
  });

  it('shows active stage and review state on spec campaign cards', async () => {
    render(<SpecsCenterPage />, {
      wrapper: wrap({ 'ting.specs': makeSpecsService() }),
    });

    await waitFor(() => expect(screen.getByText('SDCP Kubernetes operator')).toBeInTheDocument());
    expect(screen.getByText('REVIEW REQUIRED')).toBeInTheDocument();
    expect(screen.getByText('PRD review required')).toBeInTheDocument();
    expect(screen.getByText('Stage')).toBeInTheDocument();
    expect(screen.getByText('Review PRD')).toBeInTheDocument();
  });

  it('renders review notes and submits changes through the existing specs service', async () => {
    const reviewCampaign = vi.fn(async () => campaign);

    render(<SpecsCampaignPage />, {
      wrapper: wrap({ 'ting.specs': makeSpecsService({ reviewCampaign }) }),
    });

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'SDCP Kubernetes operator' })).toBeInTheDocument(),
    );
    expect(screen.getByText('Review the PRD before SRD work starts.')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText(/Clarify device-plugin ownership/i)).toBeInTheDocument(),
    );

    const requestChanges = screen.getByRole('button', { name: /Request changes/i });
    expect(requestChanges).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText('Feedback for revisions'), {
      target: { value: 'Need sharper Kubernetes ownership boundaries.' },
    });
    fireEvent.click(requestChanges);

    await waitFor(() =>
      expect(reviewCampaign).toHaveBeenCalledWith('sdcp-operator', {
        decision: 'changes_requested',
        notes: 'Need sharper Kubernetes ownership boundaries.',
        gateId: 'gate-prd',
        nodeId: 'spec-prd-gate',
      }),
    );
  });
});
