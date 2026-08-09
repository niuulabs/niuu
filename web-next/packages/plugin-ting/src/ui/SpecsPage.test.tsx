import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PluginCtxProvider, ServicesProvider, type PluginCtx } from '@niuulabs/plugin-sdk';
import type { RepoRecord } from '@niuulabs/ui';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  CampaignArtifactDetail,
  CreateSpecCampaignRequest,
  ISpecsService,
  IWorkflowService,
  SpecCampaign,
  SpecCampaignDetail,
} from '../ports';
import { createMockDispatchBus } from '../adapters/mock';
import type { Workflow } from '../domain/workflow';
import { SpecsCampaignPage } from './SpecsCampaignPage';
import { SpecsCenterPage } from './SpecsCenterPage';
import { SpecsNewPage } from './SpecsNewPage';

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
      content: `# Product Requirements

## Scope

Manage and track **SDCP printers** from Kubernetes with \`Device\` resources and [protocol notes](https://example.com/sdcp).

### Device model

- Discover SDCP printers.
- Track state transitions.

1. Publish status.
2. Reconcile desired state.

| Field | Meaning |
| --- | --- |
| phase | printer lifecycle |
`,
    },
  ],
  [
    'specifications/sdcp-operator/11-prd-review.md',
    {
      ...campaign.artifacts[1]!,
      content: `# PRD review

## Notes

- Clarify device-plugin ownership before approval.
- Confirm where Kubernetes credentials live.`,
    },
  ],
]);

const manifestArtifact: CampaignArtifactDetail = {
  path: 'specifications/sdcp-operator/50-manifest.md',
  title: 'Manifest',
  updatedAt: now,
  kind: 'manifest',
  publishState: 'published',
  sourceIds: [],
  summary: 'Published manifest',
  content: '# Manifest\n\n- 10-prd.md\n- 20-srd.md',
};

const repoCatalog = {
  async getRepos(): Promise<RepoRecord[]> {
    return [
      {
        name: 'sdcp-operator',
        owner: 'niuulabs',
        cloneUrl: 'https://github.com/niuulabs/sdcp-operator.git',
        defaultBranch: 'dev',
        branches: ['dev', 'main'],
      },
      {
        name: 'printer-runtime',
        owner: 'niuulabs',
        cloneUrl: 'https://github.com/niuulabs/printer-runtime.git',
        defaultBranch: 'main',
        branches: ['main'],
      },
    ];
  },
};

const workflowService: IWorkflowService = {
  async listWorkflows(): Promise<Workflow[]> {
    return [
      {
        id: 'workflow-spec',
        name: 'Specification Stack',
        version: '1.0.0',
        description: 'PRD/SRD/SDD specification workflow',
        tags: ['specification'],
        nodes: [],
        edges: [],
        resourceBindings: [],
      },
      {
        id: 'workflow-ops',
        name: 'Operational review',
        version: '0.3.0',
        description: 'General ops workflow',
        tags: ['ops'],
        nodes: [],
        edges: [],
        resourceBindings: [],
      },
    ];
  },
  async getWorkflow() {
    return null;
  },
  async saveWorkflow(workflow) {
    return workflow;
  },
  async deleteWorkflow() {},
  async launchWorkflow() {
    return { sessionId: 'session-sdcp', sessionName: 'SDCP spec run' };
  },
};

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

    const streamOptions = mockOpenEventStream.mock.calls[0]?.[1] as {
      onEvent?: (payload: { event?: string }) => void;
    };
    expect(streamOptions.onEvent).toBeDefined();
    streamOptions.onEvent?.({ event: 'workflow.campaign.updated' });

    fireEvent.click(screen.getByRole('button', { name: /SDCP Kubernetes operator/i }));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/ting/specs/$slug',
      params: { slug: 'sdcp-operator' },
    });
  });

  it('filters spec campaigns by status and search text', async () => {
    const running = {
      ...campaign,
      id: '22222222-2222-4222-8222-222222222223',
      slug: 'sdcp-running',
      name: 'SDCP running spec',
      status: 'running' as const,
      metadata: {
        prompt: 'Running spec',
        canonical_artifacts: { prd: 'specifications/sdcp-running/10-prd.md' },
      },
      activeStageId: 'spec-srd',
      stageState: [
        {
          stageId: 'spec-srd',
          label: 'Write SRD',
          status: 'active',
          startedAt: now,
          completedAt: null,
        },
      ],
    };
    const published = {
      ...campaign,
      id: '22222222-2222-4222-8222-222222222224',
      slug: 'sdcp-published',
      name: 'SDCP published spec',
      status: 'completed' as const,
      metadata: { prompt: 'Published spec' },
      activeStageId: 'spec-manifest',
      stageState: [
        {
          stageId: 'spec-manifest',
          label: 'Publish manifest',
          status: 'complete',
          startedAt: now,
          completedAt: now,
        },
      ],
    };
    const draft = {
      ...campaign,
      id: '22222222-2222-4222-8222-222222222225',
      slug: 'sdcp-draft',
      name: 'SDCP draft spec',
      status: 'pending' as const,
      metadata: {},
      activeStageId: null,
      stageState: [],
    };
    const starting = {
      ...campaign,
      id: '22222222-2222-4222-8222-222222222228',
      slug: 'sdcp-starting',
      name: 'SDCP starting spec',
      status: 'running' as const,
      metadata: { prompt: 'Starting spec' },
      activeStageId: null,
      stageState: [],
    };

    render(<SpecsCenterPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async listCampaigns() {
            return [campaign, running, published, draft, starting];
          },
        }),
      }),
    });

    await waitFor(() => expect(screen.getByText('SDCP running spec')).toBeInTheDocument());
    expect(screen.getByText('SDCP starting spec')).toBeInTheDocument();
    expect(screen.getByText('Starting')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: /Published/i })[0]!);
    expect(screen.getByText('SDCP published spec')).toBeInTheDocument();
    expect(screen.queryByText('SDCP running spec')).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: /Draft/i })[0]!);
    expect(screen.getByText('SDCP draft spec')).toBeInTheDocument();
    expect(screen.getByText('Specification campaign')).toBeInTheDocument();
    expect(screen.getByText('Queued')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Search specs'), {
      target: { value: 'no matching spec' },
    });
    expect(screen.getByText('No specs match the current filters.')).toBeInTheDocument();
  });

  it('renders center empty, error, and navigation states', async () => {
    render(<SpecsCenterPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async listCampaigns() {
            return [];
          },
        }),
      }),
    });

    await waitFor(() =>
      expect(screen.getByText('No specs match the current filters.')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /\+ New spec/i }));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/specs/new' });
  });

  it('renders center service failures without losing the shell', async () => {
    const { unmount } = render(<SpecsCenterPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async listCampaigns() {
            throw new Error('spec list unavailable');
          },
        }),
      }),
    });

    await waitFor(() => expect(screen.getByText('spec list unavailable')).toBeInTheDocument());
    expect(screen.getByPlaceholderText('search specs')).toBeInTheDocument();
    unmount();

    render(<SpecsCenterPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async listCampaigns() {
            throw 'not an error';
          },
        }),
      }),
    });

    await waitFor(() => expect(screen.getByText('Failed to load specs.')).toBeInTheDocument());
  });

  it('uses stage fallbacks when labels and active stages are sparse', async () => {
    const unlabeled = {
      ...campaign,
      id: '22222222-2222-4222-8222-222222222226',
      slug: 'sdcp-unlabeled',
      name: 'Unlabeled spec',
      status: 'running' as const,
      metadata: { prompt: 'Sparse stage metadata' },
      activeStageId: 'spec-technical-review',
      stageState: [
        {
          stageId: 'spec-technical-review',
          label: '',
          status: 'pending' as const,
          startedAt: now,
          completedAt: null,
        },
      ],
    };
    const labeledGate = {
      ...campaign,
      id: '22222222-2222-4222-8222-222222222227',
      slug: 'sdcp-sdd-review',
      name: 'SDD review spec',
      metadata: {
        pending_workflow_gates: [
          {
            gateId: 'gate-sdd-alt',
            nodeId: 'spec-sdd-gate',
            label: 'SDD needs review',
          },
          null,
          {
            gateId: 'gate-ignored',
            nodeId: 'ops-review-gate',
            label: 'Ignored non-spec gate',
          },
        ],
      },
      activeStageId: 'spec-sdd-gate',
      stageState: [
        {
          stageId: 'spec-sdd-gate',
          label: 'Review SDD',
          status: 'blocked' as const,
          startedAt: now,
          completedAt: null,
        },
      ],
    };
    const fallbackGate = {
      ...campaign,
      id: '22222222-2222-4222-8222-222222222229',
      slug: 'sdcp-fallback-gate',
      name: 'Fallback gate spec',
      metadata: {
        pending_workflow_gates: [
          {
            gate_id: 'gate-fallback',
            node_id: 'spec-prd-gate',
          },
        ],
      },
      activeStageId: 'spec-prd-gate',
      stageState: [],
    };

    render(<SpecsCenterPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async listCampaigns() {
            return [unlabeled, labeledGate, fallbackGate];
          },
        }),
      }),
    });

    await waitFor(() => expect(screen.getByText('technical review')).toBeInTheDocument());
    expect(screen.getByText('SDD needs review')).toBeInTheDocument();
    expect(screen.getAllByText('Review required').length).toBeGreaterThan(0);
    expect(screen.queryByText('Ignored non-spec gate')).not.toBeInTheDocument();
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

    fireEvent.click(screen.getByRole('button', { name: /Approve/i }));
    await waitFor(() =>
      expect(reviewCampaign).toHaveBeenCalledWith('sdcp-operator', {
        decision: 'approve',
        notes: '',
        gateId: 'gate-prd',
        nodeId: 'spec-prd-gate',
      }),
    );
  });

  it('does not replace spec detail cache with a summary review response', async () => {
    const {
      artifacts: _artifacts,
      canonicalArtifacts: _canonicalArtifacts,
      ...summaryCampaign
    } = campaign;
    const reviewCampaign = vi.fn(async (): Promise<SpecCampaign> => summaryCampaign);

    render(<SpecsCampaignPage />, {
      wrapper: wrap({ 'ting.specs': makeSpecsService({ reviewCampaign }) }),
    });

    await waitFor(() => expect(screen.getByRole('heading', { name: 'PRD' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Approve/i }));

    await waitFor(() => expect(reviewCampaign).toHaveBeenCalled());
    expect(screen.getByRole('heading', { name: 'PRD' })).toBeInTheDocument();
    expect(screen.queryByText('Something went wrong!')).not.toBeInTheDocument();
  });

  it('opens Mimir and deletes completed specs without a pending gate', async () => {
    const completed = {
      ...campaign,
      status: 'completed' as const,
      metadata: { prompt: 'Completed spec', branch: '' },
      activeStageId: 'spec-manifest',
      stageState: [
        {
          stageId: 'spec-manifest',
          label: 'Publish manifest',
          status: 'complete',
          startedAt: now,
          completedAt: now,
        },
      ],
      artifacts: [campaign.artifacts[0]!, manifestArtifact],
      canonicalArtifacts: { prd: campaign.artifacts[0]!.path, manifest: manifestArtifact.path },
    };
    const deleteCampaign = vi.fn(async () => {});

    render(<SpecsCampaignPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async getCampaign(slug) {
            return slug === completed.slug ? completed : null;
          },
          async getArtifact(_slug, path) {
            if (path === manifestArtifact.path) return manifestArtifact;
            return artifacts.get(path) ?? null;
          },
          deleteCampaign,
        }),
      }),
    });

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'SDCP Kubernetes operator' })).toBeInTheDocument(),
    );
    expect(screen.getByText('No document review is waiting right now.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Manifest/i }));
    await waitFor(() => expect(screen.getByText(/10-prd.md/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Open in Mimir/i }));
    expect(mockSetTweak).toHaveBeenCalledWith('mimir.selectedPagePath', manifestArtifact.path);
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/mimir/pages' });

    fireEvent.click(screen.getByRole('button', { name: /Delete/i }));
    await waitFor(() => expect(deleteCampaign).toHaveBeenCalledWith('sdcp-operator'));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/specs' });
  });

  it('handles missing spec documents and missing critic notes', async () => {
    const blocked = {
      ...campaign,
      metadata: {
        prompt: 'Blocked SRD spec',
        pending_workflow_gates: [
          {
            id: 'gate-srd',
            node_id: 'spec-srd-gate',
            summary: 'SRD review required',
            instructions: '',
          },
        ],
      },
      activeStageId: 'spec-srd-gate',
      stageState: [
        {
          stageId: 'spec-srd-gate',
          label: 'Review SRD',
          status: 'blocked',
          startedAt: now,
          completedAt: null,
        },
      ],
      artifacts: [],
      canonicalArtifacts: {
        srd: 'specifications/sdcp-operator/20-srd.md',
        srd_review: 'specifications/sdcp-operator/21-srd-review.md',
      },
    };

    render(<SpecsCampaignPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async getCampaign(slug) {
            return slug === blocked.slug ? blocked : null;
          },
          async getArtifact() {
            return null;
          },
        }),
      }),
    });

    await waitFor(() => expect(screen.getByText('SRD review required')).toBeInTheDocument());
    expect(screen.getByText('Approve or request changes for this document.')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText('No document has been written yet.')).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByText('No critic notes are available yet.')).toBeInTheDocument(),
    );
  });

  it('renders fallback document titles and back navigation', async () => {
    const sparseArtifact: CampaignArtifactDetail = {
      path: 'specifications/sdcp-operator/00-intake-notes.md',
      title: '',
      updatedAt: now,
      kind: 'brief',
      publishState: 'draft',
      sourceIds: [],
      summary: '',
      content: `# Intake notes

# Extra heading

Plain fallback content.`,
    };
    const sparse = {
      ...campaign,
      metadata: { prompt: 'Sparse artifact metadata' },
      activeStageId: 'spec-brief',
      stageState: [],
      artifacts: [sparseArtifact],
      canonicalArtifacts: { brief: sparseArtifact.path },
    };

    render(<SpecsCampaignPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async getCampaign(slug) {
            return slug === sparse.slug ? sparse : null;
          },
          async getArtifact(_slug, path) {
            return path === sparseArtifact.path ? sparseArtifact : null;
          },
        }),
      }),
    });

    await waitFor(() => expect(screen.getByText('intake notes')).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: 'Extra heading' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Specs/i }));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/specs' });
  });

  it('renders additional review gate kinds and artifact fallbacks', async () => {
    const sdd = {
      ...campaign,
      metadata: {
        repo: 'niuulabs/sdcp-operator',
        branch: 'main',
        pending_workflow_gates: [
          {
            gate_id: 'gate-sdd',
            node_id: 'spec-sdd-gate',
            label: 'SDD review required',
          },
        ],
      },
      activeStageId: 'spec-sdd-gate',
      stageState: [
        {
          stageId: 'spec-sdd-gate',
          label: 'Review SDD',
          status: 'blocked' as const,
          startedAt: now,
          completedAt: null,
        },
      ],
      artifacts: [
        {
          path: 'specifications/sdcp-operator/30-sdd.md',
          title: 'SDD',
          updatedAt: now,
          kind: 'sdd',
          publishState: 'review-ready',
          sourceIds: [],
          summary: 'Design doc',
        },
      ],
      canonicalArtifacts: {
        sdd: 'specifications/sdcp-operator/30-sdd.md',
      },
    };
    const { unmount } = render(<SpecsCampaignPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async getCampaign(slug) {
            return slug === sdd.slug ? sdd : null;
          },
          async getArtifact(_slug, path) {
            if (path === 'specifications/sdcp-operator/30-sdd.md') {
              return {
                ...sdd.artifacts[0]!,
                content: '# SDD\n\nSystem design details.',
              };
            }
            return null;
          },
        }),
      }),
    });

    await waitFor(() => expect(screen.getByText('SDD review required')).toBeInTheDocument());
    expect(screen.getByText('Gate')).toBeInTheDocument();
    expect(screen.getByText('SDD')).toBeInTheDocument();
    expect(screen.getByText('niuulabs/sdcp-operator')).toBeInTheDocument();
    unmount();

    const fallbackArtifact: CampaignArtifactDetail = {
      path: 'specifications/sdcp-operator/99-breakdown.md',
      title: 'Breakdown',
      updatedAt: now,
      kind: 'breakdown',
      publishState: 'draft',
      sourceIds: [],
      summary: '',
      content: '# Breakdown\n\nFirst available artifact.',
    };
    const breakdown = {
      ...campaign,
      metadata: {
        pending_workflow_gates: [
          {
            id: 'gate-breakdown',
            node_id: 'spec-breakdown-gate',
            summary: 'Breakdown review required',
          },
        ],
      },
      activeStageId: 'spec-breakdown-gate',
      artifacts: [fallbackArtifact],
      canonicalArtifacts: {},
    };

    render(<SpecsCampaignPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async getCampaign(slug) {
            return slug === breakdown.slug ? breakdown : null;
          },
          async getArtifact(_slug, path) {
            return path === fallbackArtifact.path ? fallbackArtifact : null;
          },
        }),
      }),
    });

    await waitFor(() => expect(screen.getByText('Breakdown review required')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('First available artifact.')).toBeInTheDocument());
    expect(screen.getByText('BREAKDOWN')).toBeInTheDocument();
  });

  it('keeps unknown spec gates reviewable without forcing a document kind', async () => {
    const unknownGate = {
      ...campaign,
      sessionName: '',
      metadata: {
        pending_workflow_gates: [
          {
            node_id: 'spec-prd-gate',
            summary: 'Ignored missing gate id',
          },
          {
            id: 'gate-intake',
            node_id: 'spec-intake-gate',
            summary: 'Intake review required',
          },
        ],
      },
      activeStageId: 'spec-intake-gate',
      artifacts: [],
      canonicalArtifacts: {},
    };

    render(<SpecsCampaignPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async getCampaign(slug) {
            return slug === unknownGate.slug ? unknownGate : null;
          },
        }),
      }),
    });

    await waitFor(() => expect(screen.getByText('Intake review required')).toBeInTheDocument());
    expect(screen.getByText('No document has been written yet.')).toBeInTheDocument();
    expect(screen.getAllByText('none')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: /Open in Mimir/i }));
    expect(mockSetTweak).not.toHaveBeenCalledWith('mimir.selectedPagePath', expect.anything());
    expect(screen.getByRole('button', { name: 'session-sdcp' })).toBeInTheDocument();
  });

  it('renders detail errors and missing campaigns', async () => {
    const { unmount } = render(<SpecsCampaignPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async getCampaign() {
            throw new Error('spec backend down');
          },
        }),
      }),
    });

    await waitFor(() => expect(screen.getByText('spec backend down')).toBeInTheDocument());
    unmount();

    const { unmount: unmountNonError } = render(<SpecsCampaignPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async getCampaign() {
            throw 'not an error';
          },
        }),
      }),
    });

    await waitFor(() => expect(screen.getByText('Failed to load spec.')).toBeInTheDocument());
    unmountNonError();

    render(<SpecsCampaignPage />, {
      wrapper: wrap({
        'ting.specs': makeSpecsService({
          async getCampaign() {
            return null;
          },
        }),
      }),
    });

    await waitFor(() => expect(screen.getByText('Spec not found.')).toBeInTheDocument());
  });

  it('uses detail event refresh and session navigation', async () => {
    const location = window.location;
    const assign = vi.fn();
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...location, assign },
    });

    try {
      render(<SpecsCampaignPage />, {
        wrapper: wrap({ 'ting.specs': makeSpecsService() }),
      });

      await waitFor(() =>
        expect(
          screen.getByRole('heading', { name: 'SDCP Kubernetes operator' }),
        ).toBeInTheDocument(),
      );

      const streamOptions = mockOpenEventStream.mock.calls[0]?.[1] as {
        onEvent?: (payload: { event?: string; data: string }) => void;
      };
      expect(streamOptions.onEvent).toBeDefined();
      streamOptions.onEvent?.({ event: 'other.event', data: '{}' });
      streamOptions.onEvent?.({ event: 'workflow.campaign.updated', data: '{bad json' });
      streamOptions.onEvent?.({
        event: 'workflow.campaign.updated',
        data: JSON.stringify({ slug: 'different-spec' }),
      });
      streamOptions.onEvent?.({
        event: 'workflow.campaign.updated',
        data: JSON.stringify({ slug: 'sdcp-operator' }),
      });

      fireEvent.click(screen.getByRole('button', { name: /SDCP spec run/i }));
      expect(assign).toHaveBeenCalledWith('/volundr/sessions/session-sdcp');
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: location });
    }
  });

  it('launches a spec with the seeded spec workflow and shared repo controls', async () => {
    const requests: CreateSpecCampaignRequest[] = [];
    const specsService = makeSpecsService({
      async createCampaign(request) {
        requests.push(request);
        return campaign;
      },
    });

    render(<SpecsNewPage />, {
      wrapper: wrap({
        'ting.specs': specsService,
        'ting.workflows': workflowService,
        'ting.dispatch': createMockDispatchBus(),
        'niuu.repos': repoCatalog,
      }),
    });

    await waitFor(() =>
      expect(screen.getByRole('option', { name: /Specification Stack/i })).toBeInTheDocument(),
    );
    expect(screen.queryByRole('option', { name: /Operational review/i })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText('Execution target')).toHaveValue('cluster-mini'),
    );
    expect(screen.getByRole('button', { name: /Launch spec/i })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: /All workflows/i }));
    await waitFor(() =>
      expect(screen.getByRole('option', { name: /Operational review/i })).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText('Workflow'), {
      target: { value: 'workflow-ops' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Spec workflows/i }));
    await waitFor(() =>
      expect(screen.queryByRole('option', { name: /Operational review/i })).not.toBeInTheDocument(),
    );

    fireEvent.change(screen.getByTestId('spec-launch-repo-select'), {
      target: { value: 'https://github.com/niuulabs/sdcp-operator.git' },
    });
    await waitFor(() => expect(screen.getByDisplayValue('dev')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('spec-launch-branch-select'), {
      target: { value: 'main' },
    });
    fireEvent.change(screen.getByTestId('spec-launch-branch-select'), {
      target: { value: 'dev' },
    });

    fireEvent.change(screen.getByLabelText('Title'), {
      target: { value: 'SDCP Kubernetes operator' },
    });
    fireEvent.change(screen.getByLabelText('Brief'), {
      target: { value: 'Specify a Kubernetes operator for SDCP 3D printers.' },
    });
    fireEvent.change(screen.getByLabelText('Context'), {
      target: { value: 'Three phases: core, device plugin, deployment.' },
    });
    fireEvent.change(screen.getByLabelText('Execution target'), {
      target: { value: 'cluster-macbook' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Launch spec/i }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({
      prompt: 'Specify a Kubernetes operator for SDCP 3D printers.',
      name: 'SDCP Kubernetes operator',
      workflowId: 'workflow-spec',
      repos: ['https://github.com/niuulabs/sdcp-operator.git'],
      repo: 'https://github.com/niuulabs/sdcp-operator.git',
      branch: 'dev',
      context: 'Three phases: core, device plugin, deployment.',
      connectionId: 'cluster-macbook',
    });
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/ting/specs/$slug',
      params: { slug: 'sdcp-operator' },
    });
  });

  it('allows backend defaults and manual repository context for specs', async () => {
    const requests: CreateSpecCampaignRequest[] = [];
    const specsService = makeSpecsService({
      async createCampaign(request) {
        requests.push(request);
        return campaign;
      },
    });
    const emptyWorkflowService: IWorkflowService = {
      ...workflowService,
      async listWorkflows() {
        return [];
      },
    };
    const emptyRepoCatalog = {
      async getRepos(): Promise<RepoRecord[]> {
        return [];
      },
    };

    render(<SpecsNewPage />, {
      wrapper: wrap({
        'ting.specs': specsService,
        'ting.workflows': emptyWorkflowService,
        'ting.dispatch': createMockDispatchBus(),
        'niuu.repos': emptyRepoCatalog,
      }),
    });

    await waitFor(() =>
      expect(screen.getByRole('option', { name: /Backend default workflow/i })).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText('Brief'), {
      target: { value: 'Write the spec without an attached repository.' },
    });
    fireEvent.change(screen.getByPlaceholderText('optional repository context'), {
      target: { value: 'https://github.com/niuulabs/later.git' },
    });
    fireEvent.blur(screen.getByPlaceholderText('optional repository context'));
    fireEvent.change(screen.getByLabelText('Branch'), {
      target: { value: 'feature/spec' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Launch spec/i }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({
      prompt: 'Write the spec without an attached repository.',
      workflowId: undefined,
      repos: ['https://github.com/niuulabs/later.git'],
      repo: 'https://github.com/niuulabs/later.git',
      branch: 'feature/spec',
      connectionId: 'cluster-mini',
    });

    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/specs' });
  });

  it('launches with backend target defaults and removes manual repositories', async () => {
    const requests: CreateSpecCampaignRequest[] = [];
    const specsService = makeSpecsService({
      async createCampaign(request) {
        requests.push(request);
        return campaign;
      },
    });
    const tagSpecWorkflow: IWorkflowService = {
      ...workflowService,
      async listWorkflows() {
        return [
          {
            id: 'workflow-plain',
            name: 'Plain docs flow',
            version: '',
            description: 'Not a tagged spec workflow',
            nodes: [],
            edges: [],
            resourceBindings: [],
          },
          {
            id: 'workflow-tag-spec',
            name: 'Custom docs flow',
            version: '',
            description: 'Spec workflow by tag',
            tags: ['spec'],
            nodes: [],
            edges: [],
            resourceBindings: [],
          },
        ];
      },
    };
    const emptyRepoCatalog = {
      async getRepos(): Promise<RepoRecord[]> {
        return [];
      },
    };
    const noTargetDispatch = {
      async getClusters() {
        return [];
      },
    };

    render(<SpecsNewPage />, {
      wrapper: wrap({
        'ting.specs': specsService,
        'ting.workflows': tagSpecWorkflow,
        'ting.dispatch': noTargetDispatch,
        'niuu.repos': emptyRepoCatalog,
      }),
    });

    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'Custom docs flow' })).toBeInTheDocument(),
    );
    expect(screen.getByLabelText('Execution target')).toHaveValue('');
    expect(screen.getByLabelText('Execution target')).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Brief'), {
      target: { value: 'Write specs with no execution target selected.' },
    });
    const manualRepo = screen.getByPlaceholderText('optional repository context');
    fireEvent.blur(manualRepo);
    fireEvent.change(manualRepo, {
      target: { value: 'https://github.com/niuulabs/remove-me.git' },
    });
    fireEvent.blur(manualRepo);
    fireEvent.change(manualRepo, {
      target: { value: 'https://github.com/niuulabs/remove-me.git' },
    });
    fireEvent.blur(manualRepo);
    fireEvent.click(screen.getByRole('button', { name: /remove-me.git/i }));
    fireEvent.click(screen.getByRole('button', { name: /Launch spec/i }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({
      prompt: 'Write specs with no execution target selected.',
      workflowId: 'workflow-tag-spec',
      repos: [],
      repo: '',
      connectionId: undefined,
    });
  });

  it('shows tagged execution targets and launch pending state', async () => {
    const specsService = makeSpecsService({
      async createCampaign() {
        return new Promise<SpecCampaignDetail>(() => {});
      },
    });
    const taggedDispatch = {
      async getClusters() {
        return [
          {
            connectionId: 'disabled-target',
            name: 'Disabled',
            enabled: false,
          },
          {
            connectionId: 'gpu-target',
            name: 'GPU target',
            enabled: true,
            tags: ['gpu', 'ymir'],
          },
        ];
      },
    };

    render(<SpecsNewPage />, {
      wrapper: wrap({
        'ting.specs': specsService,
        'ting.workflows': workflowService,
        'ting.dispatch': taggedDispatch,
        'niuu.repos': repoCatalog,
      }),
    });

    await waitFor(() => expect(screen.getByText('gpu, ymir')).toBeInTheDocument());
    expect(screen.queryByRole('option', { name: /Disabled/i })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Brief'), {
      target: { value: 'Launch and show pending state.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Launch spec/i }));

    await waitFor(() => expect(screen.getByRole('button', { name: /Launching/i })).toBeDisabled());
  });
});
