import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  CampaignArtifactSummary,
  IResearchService,
  ResearchCampaign,
  ResearchCampaignDetail,
} from '../ports';
import { createMockDispatcherService } from '../adapters/mock';
import { ResearchCenterPage } from './ResearchCenterPage';

const mockNavigate = vi.fn();
const mockOpenEventStream = vi.hoisted(() => vi.fn());

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('@niuulabs/query', () => ({
  openEventStream: mockOpenEventStream,
}));

const now = '2026-05-25T12:00:00.000Z';

const campaignDetails: ResearchCampaignDetail[] = [
  {
    id: 'camp-running',
    slug: 'rag-landscape',
    name: 'RAG landscape',
    ownerId: 'dev-user',
    workflowId: 'wf-research',
    workflowVersion: '1.0.0',
    workflowName: 'Research Campaign',
    sessionId: 'run/rag-landscape',
    sessionName: 'RAG landscape',
    status: 'running',
    activeStageId: 'challenge',
    stageState: [
      { stageId: 'frame', label: 'Frame', status: 'complete', startedAt: now, completedAt: now },
      {
        stageId: 'explore',
        label: 'Explore',
        status: 'complete',
        startedAt: now,
        completedAt: now,
      },
      {
        stageId: 'challenge',
        label: 'Challenge',
        status: 'active',
        startedAt: now,
        completedAt: null,
      },
    ],
    metadata: {
      question: 'What does the RAG tooling landscape look like?',
      mode: 'exploratory',
    },
    createdAt: now,
    updatedAt: now,
    lastActivityAt: now,
    completedAt: null,
    artifacts: [
      {
        path: 'research/campaigns/rag-landscape/brief.md',
        title: 'Brief',
        updatedAt: now,
        kind: 'brief',
        publishState: 'unknown',
        sourceIds: [],
        summary: 'Brief',
      },
      {
        path: 'research/campaigns/rag-landscape/final.md',
        title: 'Final',
        updatedAt: now,
        kind: 'final',
        publishState: 'review-ready',
        sourceIds: ['src-1', 'src-2', 'src-3'],
        summary: 'Draft answer',
      },
    ],
    canonicalArtifacts: {
      final: 'research/campaigns/rag-landscape/final.md',
    },
  },
  {
    id: 'camp-published',
    slug: 'yaml-json-prompts',
    name: 'YAML vs JSON prompt files',
    ownerId: 'dev-user',
    workflowId: 'wf-research',
    workflowVersion: '1.0.0',
    workflowName: 'Research Campaign',
    sessionId: 'run/yaml-json-prompts',
    sessionName: 'YAML vs JSON prompt files',
    status: 'completed',
    activeStageId: 'publish',
    stageState: [
      { stageId: 'frame', label: 'Frame', status: 'complete', startedAt: now, completedAt: now },
      {
        stageId: 'explore',
        label: 'Explore',
        status: 'complete',
        startedAt: now,
        completedAt: now,
      },
      {
        stageId: 'publish',
        label: 'Publish',
        status: 'complete',
        startedAt: now,
        completedAt: now,
      },
    ],
    metadata: {
      question: 'Should personas move from YAML to JSON?',
      mode: 'evaluative',
    },
    createdAt: now,
    updatedAt: now,
    lastActivityAt: now,
    completedAt: now,
    artifacts: [
      {
        path: 'research/campaigns/yaml-json-prompts/final.md',
        title: 'Final',
        updatedAt: now,
        kind: 'final',
        publishState: 'published',
        sourceIds: ['src-4', 'src-5'],
        summary: 'Published answer',
      },
      {
        path: 'research/campaigns/yaml-json-prompts/manifest.md',
        title: 'Manifest',
        updatedAt: now,
        kind: 'manifest',
        publishState: 'published',
        sourceIds: [],
        summary: 'Published set',
      },
    ],
    canonicalArtifacts: {
      final: 'research/campaigns/yaml-json-prompts/final.md',
      manifest: 'research/campaigns/yaml-json-prompts/manifest.md',
    },
  },
  {
    id: 'camp-failed',
    slug: 'rate-limit-drift',
    name: 'Anthropic rate-limit drift',
    ownerId: 'dev-user',
    workflowId: 'wf-research',
    workflowVersion: '1.0.0',
    workflowName: 'Research Campaign',
    sessionId: 'run/rate-limit-drift',
    sessionName: 'Anthropic rate-limit drift',
    status: 'failed',
    activeStageId: 'challenge',
    stageState: [
      { stageId: 'frame', label: 'Frame', status: 'complete', startedAt: now, completedAt: now },
      {
        stageId: 'challenge',
        label: 'Challenge',
        status: 'failed',
        startedAt: now,
        completedAt: now,
        reason: 'tool budget exceeded during evidence collection',
      },
    ],
    metadata: {
      question: 'What drift have we seen in Anthropic limits?',
      mode: 'monitoring',
    },
    createdAt: now,
    updatedAt: now,
    lastActivityAt: now,
    completedAt: now,
    artifacts: [],
    canonicalArtifacts: {},
  },
  {
    id: 'camp-draft',
    slug: 'homelab-power-budget',
    name: 'Homelab power budget',
    ownerId: 'dev-user',
    workflowId: 'wf-research',
    workflowVersion: '1.0.0',
    workflowName: 'Research Campaign',
    sessionId: 'run/homelab-power-budget',
    sessionName: 'Homelab power budget',
    status: 'pending',
    activeStageId: 'frame',
    stageState: [],
    metadata: {
      question: 'Can the printer rack handle one more machine?',
      mode: 'investigative',
    },
    createdAt: now,
    updatedAt: now,
    lastActivityAt: now,
    completedAt: null,
    artifacts: [],
    canonicalArtifacts: {},
  },
];

/**
 * Mirror the server's artifact summary, which the campaign list now carries.
 *
 * The cards used to derive these counts by fetching each campaign's detail;
 * they come down with the list instead, so the fake list must supply them.
 */
function summarize(campaign: ResearchCampaignDetail): CampaignArtifactSummary {
  const kindOf = (artifact: { path: string; kind?: string | null }) =>
    (artifact.kind ?? '').toLowerCase() || artifact.path.toLowerCase();
  const sourceIds = new Set<string>();
  for (const artifact of campaign.artifacts) {
    for (const id of artifact.sourceIds ?? []) sourceIds.add(id);
  }
  return {
    artifactCount: campaign.artifacts.length,
    sourceCount: sourceIds.size,
    critiqueCount: campaign.artifacts.filter((a) =>
      ['critique', 'challenge', 'skeptic'].some((k) => kindOf(a).includes(k)),
    ).length,
    learningCount: campaign.artifacts.filter(
      (a) => kindOf(a).includes('learning') || a.path.startsWith('learnings/'),
    ).length,
    followUpCount: campaign.artifacts.filter(
      (a) => kindOf(a).includes('followup') || a.path.startsWith('followups/'),
    ).length,
    published:
      'manifest' in campaign.canonicalArtifacts ||
      campaign.artifacts.some((a) => a.publishState === 'published'),
    known: true,
  };
}

const researchService: IResearchService = {
  async listCampaigns() {
    return campaignDetails.map((campaign) => ({
      ...campaign,
      artifactSummary: summarize(campaign),
    }));
  },
  async getCampaign(slug) {
    return campaignDetails.find((campaign) => campaign.slug === slug) ?? null;
  },
  async createCampaign() {
    return campaignDetails[0]!;
  },
  async updateCampaign() {
    return campaignDetails[0]!;
  },
  async deleteCampaign() {},
  async listArtifacts(slug) {
    return campaignDetails.find((campaign) => campaign.slug === slug)?.artifacts ?? [];
  },
  async getArtifact() {
    return null;
  },
};

function createResearchService(
  campaigns: ResearchCampaignDetail[],
  options: {
    missingSlugs?: string[];
    listError?: unknown;
  } = {},
): IResearchService {
  const { missingSlugs = [], listError } = options;
  const missing = new Set(missingSlugs);
  return {
    async listCampaigns() {
      if (listError) throw listError;
      // A slug listed as missing stands for a campaign the server could not
      // summarise, which must read as unknown rather than as having none.
      return campaigns.map((campaign) => ({
        ...campaign,
        artifactSummary: missing.has(campaign.slug) ? null : summarize(campaign),
      }));
    },
    async getCampaign(slug) {
      if (missing.has(slug)) return null;
      return campaigns.find((campaign) => campaign.slug === slug) ?? null;
    },
    async createCampaign() {
      return campaigns[0]!;
    },
    async updateCampaign() {
      return campaigns[0]!;
    },
    async deleteCampaign() {},
    async listArtifacts(slug) {
      return campaigns.find((campaign) => campaign.slug === slug)?.artifacts ?? [];
    },
    async getArtifact() {
      return null;
    },
  };
}

function wrap(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('ResearchCenterPage', () => {
  beforeEach(() => {
    mockNavigate.mockClear();
    mockOpenEventStream.mockReset();
    mockOpenEventStream.mockReturnValue({ close: vi.fn() });
  });

  it('renders the research dashboard and can switch to table mode', async () => {
    render(<ResearchCenterPage />, {
      wrapper: wrap({
        'ting.research': researchService,
        'ting.dispatcher': createMockDispatcherService(),
      }),
    });

    await waitFor(() => expect(screen.getByText(/RAG landscape/i)).toBeInTheDocument());
    expect(screen.getByText('Needs attention')).toBeInTheDocument();
    expect(screen.getByText('Published')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/search by title, slug, question/i), {
      target: { value: 'yaml' },
    });
    await waitFor(() => expect(screen.getByText(/YAML vs JSON prompt files/i)).toBeInTheDocument());
    expect(screen.queryByText(/RAG landscape/i)).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/search by title, slug, question/i), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Needs attention/i }));
    await waitFor(() =>
      expect(screen.getByText(/Anthropic rate-limit drift/i)).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /Published/i }));
    await waitFor(() => expect(screen.getByText(/YAML vs JSON prompt files/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Confidence' }));
    fireEvent.click(screen.getByRole('button', { name: /All/i }));
    fireEvent.click(screen.getByRole('button', { name: '+ New campaign' }));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/research/new' });

    fireEvent.click(screen.getByRole('tab', { name: 'Table' }));
    expect(screen.getByRole('columnheader', { name: 'Campaign' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Status' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Homelab power budget/i }));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/ting/research/$slug',
      params: { slug: 'homelab-power-budget' },
    });
  });

  it('shows empty and error states when filters remove campaigns or loading fails', async () => {
    const brokenResearchService: IResearchService = {
      async listCampaigns() {
        throw new Error('research index offline');
      },
      async getCampaign() {
        return null;
      },
      async createCampaign() {
        throw new Error('not needed');
      },
      async updateCampaign() {
        throw new Error('not needed');
      },
      async deleteCampaign() {},
      async listArtifacts() {
        return [];
      },
      async getArtifact() {
        return null;
      },
    };

    const view = render(<ResearchCenterPage />, {
      wrapper: wrap({
        'ting.research': researchService,
        'ting.dispatcher': createMockDispatcherService(),
      }),
    });

    await waitFor(() => expect(screen.getByText(/RAG landscape/i)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/search by title, slug, question/i), {
      target: { value: 'does-not-exist' },
    });
    await waitFor(() =>
      expect(screen.getByText(/No campaigns match the current filters\./i)).toBeInTheDocument(),
    );

    view.unmount();

    render(<ResearchCenterPage />, {
      wrapper: wrap({
        'ting.research': brokenResearchService,
        'ting.dispatcher': createMockDispatcherService(),
      }),
    });

    await waitFor(() => expect(screen.getByText(/research index offline/i)).toBeInTheDocument());
  });

  it('covers derived branch states and helper fallbacks through rendered campaign cards', async () => {
    const branchCampaigns: ResearchCampaignDetail[] = [
      {
        id: 'camp-blocked',
        slug: 'blocked-branch-campaign',
        name: 'Blocked branch campaign',
        ownerId: 'dev-user',
        workflowId: 'wf-research',
        workflowVersion: '1.0.0',
        workflowName: 'Research Campaign',
        sessionId: 'abcdefghijk',
        sessionName: 'Blocked branch campaign',
        status: 'blocked',
        activeStageId: 'review',
        stageState: [
          {
            stageId: 'review',
            label: 'Review',
            status: 'gated',
            startedAt: '2026-05-23T09:00:00.000Z',
            completedAt: null,
          },
        ],
        metadata: {
          question: '   ',
          mode: '',
        },
        createdAt: '2026-05-23T09:00:00.000Z',
        updatedAt: '2026-05-25T12:00:00.000Z',
        lastActivityAt: '2026-05-25T12:00:00.000Z',
        completedAt: null,
        artifacts: [
          {
            path: 'research/campaigns/blocked-branch-campaign/sources.md',
            title: 'Sources',
            updatedAt: now,
            kind: null,
            publishState: 'unknown',
            sourceIds: ['src-a', 'src-b'],
            summary: 'Sources',
          },
          {
            path: 'research/campaigns/blocked-branch-campaign/challenge.md',
            title: 'Challenge',
            updatedAt: now,
            kind: null,
            publishState: 'unknown',
            sourceIds: ['src-b'],
            summary: 'Challenge',
          },
          {
            path: 'learnings/blocked-branch-campaign.md',
            title: 'Learn',
            updatedAt: now,
            kind: null,
            publishState: 'unknown',
            sourceIds: [],
            summary: 'Learn',
          },
          {
            path: 'followups/blocked-branch-campaign.md',
            title: 'Followup',
            updatedAt: now,
            kind: null,
            publishState: 'unknown',
            sourceIds: [],
            summary: 'Followup',
          },
        ],
        canonicalArtifacts: {},
      },
      {
        id: 'camp-review-ready',
        slug: 'review-ready-branch',
        name: 'Review ready branch',
        ownerId: 'dev-user',
        workflowId: 'wf-research',
        workflowVersion: '1.0.0',
        workflowName: 'Research Campaign',
        sessionId: 'reviewready123',
        sessionName: 'Review ready branch',
        status: 'completed',
        activeStageId: 'publish',
        stageState: [
          {
            stageId: 'frame',
            label: 'Frame',
            status: 'complete',
            startedAt: '2026-05-25T10:30:00.000Z',
            completedAt: '2026-05-25T11:00:00.000Z',
          },
          {
            stageId: 'publish',
            label: 'Publish',
            status: 'gated',
            startedAt: '2026-05-25T11:00:00.000Z',
            completedAt: null,
          },
        ],
        metadata: {
          question: 'Is the draft ready for publication?',
          mode: 'deep_monitoring',
        } as ResearchCampaign['metadata'],
        createdAt: '2026-05-25T10:30:00.000Z',
        updatedAt: '2026-05-25T12:00:00.000Z',
        lastActivityAt: '2026-05-25T12:00:00.000Z',
        completedAt: '2026-05-25T12:00:00.000Z',
        artifacts: [
          {
            path: 'research/campaigns/review-ready-branch/final.md',
            title: 'Final',
            updatedAt: now,
            kind: 'final',
            publishState: 'review-ready',
            sourceIds: ['src-c'],
            summary: 'Draft answer',
          },
        ],
        canonicalArtifacts: {
          final: 'research/campaigns/review-ready-branch/final.md',
        },
      },
      {
        id: 'camp-published-learning',
        slug: 'published-via-learning',
        name: 'Published via learning',
        ownerId: 'dev-user',
        workflowId: 'wf-research',
        workflowVersion: '1.0.0',
        workflowName: 'Research Campaign',
        sessionId: 'published888',
        sessionName: 'Published via learning',
        status: 'completed',
        activeStageId: 'publish',
        stageState: [
          {
            stageId: 'publish',
            label: 'Publish',
            status: 'complete',
            startedAt: '2026-05-25T11:59:30.000Z',
            completedAt: '2026-05-25T12:00:00.000Z',
          },
        ],
        metadata: {
          question: 'What should we remember from this run?',
          mode: 'evaluative',
        },
        createdAt: '2026-05-25T11:59:30.000Z',
        updatedAt: '2026-05-25T12:00:00.000Z',
        lastActivityAt: '2026-05-25T12:00:00.000Z',
        completedAt: '2026-05-25T12:00:00.000Z',
        artifacts: [
          {
            path: 'learnings/published-via-learning.md',
            title: 'Learning',
            updatedAt: now,
            kind: null,
            publishState: 'published',
            sourceIds: ['src-d'],
            summary: 'Published learning',
          },
        ],
        canonicalArtifacts: {},
      },
      {
        id: 'camp-running-live',
        slug: 'running-without-detail',
        name: 'Running without detail',
        ownerId: 'dev-user',
        workflowId: 'wf-research',
        workflowVersion: '1.0.0',
        workflowName: 'Research Campaign',
        sessionId: 'live999999',
        sessionName: 'Running without detail',
        status: 'running',
        activeStageId: 'launch',
        stageState: [],
        metadata: {
          question: 'Waiting on artifacts?',
          mode: 'monitoring',
        },
        createdAt: '2026-05-25T11:00:00.000Z',
        updatedAt: '2026-05-25T12:00:00.000Z',
        lastActivityAt: '2026-05-25T12:00:00.000Z',
        completedAt: null,
        artifacts: [],
        canonicalArtifacts: {},
      },
      {
        id: 'camp-draft-branch',
        slug: 'draft-created-latest',
        name: 'Draft created latest',
        ownerId: 'dev-user',
        workflowId: 'wf-research',
        workflowVersion: '1.0.0',
        workflowName: 'Research Campaign',
        sessionId: 'draft123456',
        sessionName: 'Draft created latest',
        status: 'pending',
        activeStageId: 'frame',
        stageState: [],
        metadata: {
          question: 'Newest draft first?',
          mode: 'investigative',
        },
        createdAt: '2026-05-25T11:58:00.000Z',
        updatedAt: '2026-05-25T11:58:00.000Z',
        lastActivityAt: '2026-05-25T11:58:00.000Z',
        completedAt: null,
        artifacts: [],
        canonicalArtifacts: {},
      },
    ];

    const branchResearchService = createResearchService(branchCampaigns, {
      missingSlugs: ['running-without-detail'],
    });
    const pausedDispatcherService = {
      async getState() {
        return { running: false };
      },
    };

    const view = render(<ResearchCenterPage />, {
      wrapper: wrap({
        'ting.research': branchResearchService,
        'ting.dispatcher': pausedDispatcherService,
      }),
    });

    await waitFor(() => expect(screen.getByText(/Blocked branch campaign/i)).toBeInTheDocument());
    expect(
      screen.getByText((_, element) => element?.textContent === 'dispatcher paused'),
    ).toBeInTheDocument();
    expect(screen.getByText('BLOCKED')).toBeInTheDocument();
    expect(screen.getAllByText('REVIEW-READY').length).toBeGreaterThan(0);
    expect(screen.getAllByText('PUBLISHED').length).toBeGreaterThan(0);
    expect(screen.getByText('EXPLORATORY')).toBeInTheDocument();
    expect(screen.getByText('DEEP MONITORING')).toBeInTheDocument();
    expect(
      screen.getByText('Review campaign artifacts and published memory in Mimir.'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('campaign needs human review before it can continue'),
    ).toBeInTheDocument();
    expect(screen.getByText('awaiting artifact updates from live workflow')).toBeInTheDocument();
    expect(screen.getByText('2d 3h')).toBeInTheDocument();
    expect(screen.getByText('1h 0m')).toBeInTheDocument();
    expect(screen.getByText('30s')).toBeInTheDocument();
    expect(screen.getByText('2 src')).toBeInTheDocument();
    expect(screen.getByText('1 crit')).toBeInTheDocument();
    expect(screen.getAllByText('1 learn').length).toBeGreaterThan(0);
    expect(screen.getAllByText('1 f/u').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /Drafts/i }));
    await waitFor(() => expect(screen.getByText(/Draft created latest/i)).toBeInTheDocument());
    expect(screen.queryByText(/Blocked branch campaign/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /All/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Created' }));
    fireEvent.click(screen.getByRole('tab', { name: 'Table' }));

    const orderedLinks = Array.from(view.container.querySelectorAll('.research-table__link')).map(
      (element) => element.textContent,
    );
    expect(orderedLinks).toEqual([
      'Published via learning',
      'Draft created latest',
      'Running without detail',
      'Review ready branch',
      'Blocked branch campaign',
    ]);
  });

  it('invalidates the index on campaign events and supports opening grid cards directly', async () => {
    const listCampaigns = vi.fn(async () => campaignDetails.map((campaign) => ({ ...campaign })));
    const eventfulResearchService: IResearchService = {
      ...researchService,
      listCampaigns,
    };

    render(<ResearchCenterPage />, {
      wrapper: wrap({
        'ting.research': eventfulResearchService,
        'ting.dispatcher': createMockDispatcherService(),
      }),
    });

    await waitFor(() => expect(screen.getByText(/RAG landscape/i)).toBeInTheDocument());
    const initialCalls = listCampaigns.mock.calls.length;
    expect(initialCalls).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /RAG landscape/i }));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/ting/research/$slug',
      params: { slug: 'rag-landscape' },
    });

    mockOpenEventStream.mock.calls[0]?.[1].onEvent?.({
      event: 'workflow.campaign.updated',
      data: '{}',
    });

    await waitFor(() => expect(listCampaigns.mock.calls.length).toBeGreaterThan(initialCalls));
  });
});
