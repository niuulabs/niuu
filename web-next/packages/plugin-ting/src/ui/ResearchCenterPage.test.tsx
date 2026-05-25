import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { IResearchService, ResearchCampaignDetail } from '../ports';
import { createMockDispatcherService } from '../adapters/mock';
import { ResearchCenterPage } from './ResearchCenterPage';

const mockNavigate = vi.fn();

class MockEventSource {
  static instances: MockEventSource[] = [];
  private listeners = new Map<string, Array<() => void>>();

  constructor() {
    MockEventSource.instances.push(this);
  }

  addEventListener = vi.fn((event: string, listener: () => void) => {
    const existing = this.listeners.get(event) ?? [];
    existing.push(listener);
    this.listeners.set(event, existing);
  });
  close = vi.fn();

  emit(event: string) {
    for (const listener of this.listeners.get(event) ?? []) listener();
  }
}

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
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

const researchService: IResearchService = {
  async listCampaigns() {
    return campaignDetails.map((campaign) => ({ ...campaign }));
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
    vi.stubGlobal('EventSource', MockEventSource);
    MockEventSource.instances = [];
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
    await waitFor(() =>
      expect(screen.getByText(/YAML vs JSON prompt files/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/RAG landscape/i)).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/search by title, slug, question/i), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Needs attention/i }));
    await waitFor(() =>
      expect(screen.getByText(/Anthropic rate-limit drift/i)).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /Published/i }));
    await waitFor(() =>
      expect(screen.getByText(/YAML vs JSON prompt files/i)).toBeInTheDocument(),
    );

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

    await waitFor(() =>
      expect(screen.getByText(/research index offline/i)).toBeInTheDocument(),
    );
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

    MockEventSource.instances[0]?.emit('workflow.campaign.updated');

    await waitFor(() => expect(listCampaigns.mock.calls.length).toBeGreaterThan(initialCalls));
  });
});
