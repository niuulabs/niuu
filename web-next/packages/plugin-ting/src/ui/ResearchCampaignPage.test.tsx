import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PluginCtxProvider, ServicesProvider, type PluginCtx } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import type { CampaignArtifactDetail, ResearchCampaignDetail } from '../domain/research';
import type { IDispatcherService, IResearchService } from '../ports';
import { createMockDispatcherService } from '../adapters/mock';
import { ResearchCampaignPage } from './ResearchCampaignPage';

const mockNavigate = vi.fn();
const mockSetTweak = vi.fn();
const mockOpenEventStream = vi.hoisted(() => vi.fn());
let mockSlug = 'local-model-serving';

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useParams: () => ({ slug: mockSlug }),
}));

vi.mock('@niuulabs/query', () => ({
  openEventStream: mockOpenEventStream,
}));

const now = '2026-05-25T12:00:00.000Z';

const campaign: ResearchCampaignDetail = {
  id: '11111111-1111-4111-8111-111111111112',
  slug: 'local-model-serving',
  name: 'Local model serving on the homelab',
  ownerId: 'lars',
  workflowId: '00000000-0000-4000-8000-000000000001',
  workflowVersion: '1.0.0',
  workflowName: 'Research Campaign',
  sessionId: 'run/c1-glitnir',
  sessionName: 'Local model serving on the homelab',
  status: 'completed',
  activeStageId: 'publish',
  stageState: [
    {
      stageId: 'frame',
      label: 'Frame the inquiry',
      status: 'complete',
      startedAt: now,
      completedAt: now,
    },
    {
      stageId: 'explore',
      label: 'Explore the evidence',
      status: 'complete',
      startedAt: now,
      completedAt: now,
    },
    {
      stageId: 'challenge',
      label: 'Challenge the thesis',
      status: 'complete',
      startedAt: now,
      completedAt: now,
    },
    {
      stageId: 'publish',
      label: 'Publish to Mimir',
      status: 'complete',
      startedAt: now,
      completedAt: now,
    },
  ],
  metadata: {
    question:
      'Can a self-hosted vLLM + sglang stack absorb the memory and strength raven personas?',
    mode: 'evaluative',
    audience: 'Niuu ops + bifrost owners',
    deliverable: 'Decision memo',
  },
  createdAt: now,
  updatedAt: now,
  lastActivityAt: now,
  completedAt: now,
  artifacts: [
    {
      path: 'research/campaigns/local-model-serving/final.md',
      title: 'Final synthesis',
      updatedAt: now,
      kind: 'final',
      publishState: 'published',
      sourceIds: ['src_1', 'src_2'],
      summary: 'Published final answer',
    },
    {
      path: 'research/campaigns/local-model-serving/critique.md',
      title: 'Critique',
      updatedAt: now,
      kind: 'critique',
      publishState: 'review-ready',
      sourceIds: [],
      summary: 'Skeptic pass',
    },
    {
      path: 'research/campaigns/local-model-serving/manifest.md',
      title: 'Manifest',
      updatedAt: now,
      kind: 'manifest',
      publishState: 'published',
      sourceIds: [],
      summary: 'Published artifacts',
    },
  ],
  canonicalArtifacts: {
    final: 'research/campaigns/local-model-serving/final.md',
    critique: 'research/campaigns/local-model-serving/critique.md',
    manifest: 'research/campaigns/local-model-serving/manifest.md',
  },
};

const artifactDetails = new Map<string, CampaignArtifactDetail>([
  [
    'research/campaigns/local-model-serving/final.md',
    {
      ...campaign.artifacts[0]!,
      content: `---
source_ids: [src_1, src_2]
---
# Final synthesis

## Answer in one paragraph

A two-engine local serving stack can absorb memory persona traffic with acceptable p95 latency. [s1] [s2]

## Recommendation

1. Adopt vLLM on Spark-A for memory persona traffic. [s1]

Critique [c1] is correct that benchmarking needs to happen before rollout.
`,
    },
  ],
  [
    'research/campaigns/local-model-serving/critique.md',
    {
      ...campaign.artifacts[1]!,
      content: `# Skeptic's pass

## [c1] Throughput numbers are aspirational

against: current thesis
severity: high

Need a measured benchmark before rollout.`,
    },
  ],
  [
    'research/campaigns/local-model-serving/manifest.md',
    {
      ...campaign.artifacts[2]!,
      content: `# Manifest\n\n- final.md\n- critique.md`,
    },
  ],
]);

const runningCampaign: ResearchCampaignDetail = {
  ...campaign,
  id: '11111111-1111-4111-8111-111111111113',
  slug: 'homelab-running',
  name: 'Local model serving in progress',
  sessionId: 'run/c2-noatun',
  sessionName: 'Local model serving in progress',
  status: 'running',
  activeStageId: 'challenge',
  stageState: [
    {
      stageId: 'frame',
      label: 'Frame the inquiry',
      status: 'complete',
      startedAt: now,
      completedAt: now,
    },
    {
      stageId: 'explore',
      label: 'Explore the evidence',
      status: 'complete',
      startedAt: now,
      completedAt: now,
    },
    {
      stageId: 'challenge',
      label: 'Challenge the thesis',
      status: 'active',
      startedAt: now,
      completedAt: null,
    },
  ],
  artifacts: [
    {
      path: 'research/campaigns/homelab-running/analysis.md',
      title: 'Analysis',
      updatedAt: now,
      kind: 'analysis',
      publishState: 'unknown',
      sourceIds: ['src_1', 'src_2'],
      summary: 'Working thesis',
    },
    {
      path: 'research/campaigns/homelab-running/critique.md',
      title: 'Critique',
      updatedAt: now,
      kind: 'critique',
      publishState: 'review-ready',
      sourceIds: [],
      summary: 'Skeptic pass',
    },
  ],
  canonicalArtifacts: {
    critique: 'research/campaigns/homelab-running/critique.md',
  },
};

const runningArtifactDetails = new Map<string, CampaignArtifactDetail>([
  [
    'research/campaigns/homelab-running/analysis.md',
    {
      ...runningCampaign.artifacts[0]!,
      content: `# Working thesis

Tentative answer in one line. A two-engine local stack can likely absorb the memory persona traffic if sglang benchmarks hold. [s1]

## What's being challenged right now

- [c1] Throughput numbers are aspirational
- [c2] Sglang case is undersold
`,
    },
  ],
  [
    'research/campaigns/homelab-running/critique.md',
    {
      ...runningCampaign.artifacts[1]!,
      content: `# Skeptic's pass

## [c1] Throughput numbers are aspirational

against: current thesis
severity: high

We still need measured benchmarks on the real stack.

## [c2] Sglang case is undersold

against: current thesis
severity: med

The structured-output case maps well to our workload.`,
    },
  ],
]);

const reviewCampaign: ResearchCampaignDetail = {
  ...campaign,
  id: '11111111-1111-4111-8111-111111111114',
  slug: 'skuld-fanout-options',
  name: 'Replacing Skuld with a managed pub/sub?',
  sessionId: 'run/c3-eitri',
  sessionName: 'Replacing Skuld with a managed pub/sub?',
  status: 'completed',
  activeStageId: 'publish',
  artifacts: [
    {
      path: 'research/campaigns/skuld-fanout-options/final.md',
      title: 'Final synthesis',
      updatedAt: now,
      kind: 'final',
      publishState: 'review-ready',
      sourceIds: ['src_1', 'src_2'],
      summary: 'Awaiting publish',
    },
    {
      path: 'research/campaigns/skuld-fanout-options/critique.md',
      title: 'Critique',
      updatedAt: now,
      kind: 'critique',
      publishState: 'review-ready',
      sourceIds: [],
      summary: 'Critique',
    },
    {
      path: 'research/campaigns/skuld-fanout-options/manifest.md',
      title: 'Manifest',
      updatedAt: now,
      kind: 'manifest',
      publishState: 'review-ready',
      sourceIds: [],
      summary: 'Publish manifest',
    },
  ],
  canonicalArtifacts: {
    final: 'research/campaigns/skuld-fanout-options/final.md',
    critique: 'research/campaigns/skuld-fanout-options/critique.md',
    manifest: 'research/campaigns/skuld-fanout-options/manifest.md',
  },
};

const reviewArtifactDetails = new Map<string, CampaignArtifactDetail>([
  [
    'research/campaigns/skuld-fanout-options/final.md',
    {
      ...reviewCampaign.artifacts[0]!,
      content: `# Final synthesis

## Answer in one paragraph

Yes, there is a tactical win in replacing fan-out with a managed pub/sub if we keep the run chat semantics intact. [s1]

## Recommendation

1. Prototype a managed pub/sub bridge before touching clients. [s2]`,
    },
  ],
  [
    'research/campaigns/skuld-fanout-options/critique.md',
    {
      ...reviewCampaign.artifacts[1]!,
      content: `# Skeptic's pass

## [c1] Reliability trade-offs are underspecified

against: managed pub/sub thesis
severity: med

Need explicit failure-mode comparison before publish.`,
    },
  ],
  [
    'research/campaigns/skuld-fanout-options/manifest.md',
    {
      ...reviewCampaign.artifacts[2]!,
      content: `# Manifest\n\n- final.md\n- critique.md`,
    },
  ],
]);

const blockedCampaign: ResearchCampaignDetail = {
  ...campaign,
  id: '11111111-1111-4111-8111-111111111115',
  slug: 'printer-rack-power',
  name: 'Edge power budget for the printer rack',
  sessionId: 'run/c4-jarnvidr',
  sessionName: 'Edge power budget for the printer rack',
  status: 'blocked',
  activeStageId: 'challenge',
  stageState: [
    {
      stageId: 'frame',
      label: 'Frame the inquiry',
      status: 'complete',
      startedAt: now,
      completedAt: now,
    },
    {
      stageId: 'challenge',
      label: 'Challenge the thesis',
      status: 'blocked',
      startedAt: now,
      completedAt: null,
      reason: 'mimir write quota exceeded on /tmp/mimir',
    },
  ],
  artifacts: [],
  canonicalArtifacts: {},
};

const failedCampaign: ResearchCampaignDetail = {
  ...campaign,
  id: '11111111-1111-4111-8111-111111111117',
  slug: 'spark-throughput-failure',
  name: 'Spark cluster vs A6000 throughput',
  sessionId: 'run/c6-asgard',
  sessionName: 'Spark cluster vs A6000 throughput',
  status: 'failed',
  activeStageId: 'challenge',
  stageState: [
    {
      stageId: 'frame',
      label: 'Frame the inquiry',
      status: 'complete',
      startedAt: now,
      completedAt: now,
    },
    {
      stageId: 'explore',
      label: 'Explore the evidence',
      status: 'complete',
      startedAt: now,
      completedAt: now,
    },
    {
      stageId: 'challenge',
      label: 'Challenge the thesis',
      status: 'failed',
      startedAt: now,
      completedAt: null,
      reason: 'tool budget exceeded during source expansion',
    },
  ],
  artifacts: [],
  canonicalArtifacts: {},
};

const draftCampaign: ResearchCampaignDetail = {
  ...campaign,
  id: '11111111-1111-4111-8111-111111111116',
  slug: 'draft-idea',
  name: 'Draft campaign',
  sessionId: 'run/c5-valaskjalf',
  sessionName: 'Draft campaign',
  status: 'pending',
  activeStageId: 'frame',
  stageState: [],
  artifacts: [],
  canonicalArtifacts: {},
};

const richPublishedCampaign: ResearchCampaignDetail = {
  ...campaign,
  id: '11111111-1111-4111-8111-111111111118',
  slug: 'durable-memory-proof',
  name: 'Durable memory proof pack',
  sessionId: 'run/c7-bilskirnir',
  sessionName: 'Durable memory proof pack',
  metadata: {
    question: 'How should we package a durable memory proof pack for research?',
    mode: 'monitoring',
    audience: 'platform',
    deliverable: 'reference pack',
  },
  artifacts: [
    {
      path: 'research/campaigns/durable-memory-proof/final.md',
      title: 'Final synthesis',
      updatedAt: now,
      kind: 'final',
      publishState: 'published',
      sourceIds: ['src_file', 'src_feed', 'src_chat'],
      summary: 'Final answer',
    },
    {
      path: 'research/campaigns/durable-memory-proof/critique.md',
      title: 'Critique',
      updatedAt: now,
      kind: 'critique',
      publishState: 'review-ready',
      sourceIds: [],
      summary: 'Bullet critique',
    },
    {
      path: 'learnings/research/durable-memory-proof.md',
      title: 'Learnings',
      updatedAt: now,
      kind: 'learnings',
      publishState: 'published',
      sourceIds: [],
      summary: 'Durable learnings',
    },
    {
      path: 'followups/research/durable-memory-proof.md',
      title: 'Follow-ups',
      updatedAt: now,
      kind: 'followups',
      publishState: 'unknown',
      sourceIds: [],
      summary: 'Open follow-ups',
    },
  ],
  canonicalArtifacts: {
    final: 'research/campaigns/durable-memory-proof/final.md',
    critique: 'research/campaigns/durable-memory-proof/critique.md',
  },
};

const richArtifactDetails = new Map<string, CampaignArtifactDetail>([
  [
    'research/campaigns/durable-memory-proof/final.md',
    {
      ...richPublishedCampaign.artifacts[0]!,
      content: `---
confidence: low
source_ids: [src_file, src_feed, src_chat]
---
# Final synthesis

## Answer in one paragraph

This paragraph intentionally omits inline citations so the fallback source chips render.

### Supporting notes

Use **structured notes** with \`memory-proof\` and [operator guide](https://example.com/operator-guide).

| Artifact | Status |
| --- | --- |
| final.md | published |
| followups.md | notebook |

1. Capture learnings.
2. Publish the durable set.
`,
    },
  ],
  [
    'research/campaigns/durable-memory-proof/critique.md',
    {
      ...richPublishedCampaign.artifacts[1]!,
      content: `# Skeptic's pass

- Evidence freshness is unclear
- Monitoring cadence is underspecified`,
    },
  ],
  [
    'learnings/research/durable-memory-proof.md',
    {
      ...richPublishedCampaign.artifacts[2]!,
      content: `# Learnings

## Durable packaging
One manifest should name the durable set.

- shipping rhythm: publish after operator review`,
    },
  ],
  [
    'followups/research/durable-memory-proof.md',
    {
      ...richPublishedCampaign.artifacts[3]!,
      content: `# Follow-ups

- verify side-panel defaults: confirm deep links survive refresh`,
    },
  ],
]);

const reviewMemoryCampaign: ResearchCampaignDetail = {
  ...campaign,
  id: '11111111-1111-4111-8111-111111111119',
  slug: 'memory-review-ready',
  name: 'Memory review-ready branches',
  sessionId: 'run/c8-folkvangr',
  sessionName: 'Memory review-ready branches',
  artifacts: [
    {
      path: 'research/campaigns/memory-review-ready/final.md',
      title: 'Final synthesis',
      updatedAt: now,
      kind: 'final',
      publishState: 'published',
      sourceIds: [],
      summary: 'Published final answer',
    },
    {
      path: 'research/campaigns/memory-review-ready/manifest.md',
      title: 'Manifest',
      updatedAt: now,
      kind: 'manifest',
      publishState: 'unpublished',
      sourceIds: [],
      summary: 'Queued for review',
    },
    {
      path: 'learnings/research/memory-review-ready.md',
      title: 'Learnings',
      updatedAt: now,
      kind: 'learnings',
      publishState: 'published',
      sourceIds: [],
      summary: 'Durable learnings',
    },
    {
      path: 'followups/research/memory-review-ready.md',
      title: 'Follow-ups',
      updatedAt: now,
      kind: 'followups',
      publishState: 'unknown',
      sourceIds: [],
      summary: 'Open follow-ups',
    },
  ],
  canonicalArtifacts: {
    final: 'research/campaigns/memory-review-ready/final.md',
    manifest: 'research/campaigns/memory-review-ready/manifest.md',
  },
};

const reviewMemoryArtifactDetails = new Map<string, CampaignArtifactDetail>([
  [
    'research/campaigns/memory-review-ready/final.md',
    {
      ...reviewMemoryCampaign.artifacts[0]!,
      content: `# Final synthesis

Published final answer for durable memory testing.`,
    },
  ],
  [
    'research/campaigns/memory-review-ready/manifest.md',
    {
      ...reviewMemoryCampaign.artifacts[1]!,
      content: `# Manifest

Pending publish review.`,
    },
  ],
  [
    'learnings/research/memory-review-ready.md',
    {
      ...reviewMemoryCampaign.artifacts[2]!,
      content: `# Learnings

## Durable branch
Open the learnings artifact from here.`,
    },
  ],
  [
    'followups/research/memory-review-ready.md',
    {
      ...reviewMemoryCampaign.artifacts[3]!,
      content: `# Follow-ups

## Next branch
Open the follow-up artifact from here.`,
    },
  ],
]);

function makeResearchService(
  detail: ResearchCampaignDetail,
  artifacts: Map<string, CampaignArtifactDetail>,
  overrides?: Partial<IResearchService>,
): IResearchService {
  return {
    async listCampaigns() {
      return [detail];
    },
    async getCampaign(slug) {
      return slug === detail.slug ? detail : null;
    },
    async createCampaign() {
      return detail;
    },
    async updateCampaign() {
      return detail;
    },
    async deleteCampaign() {},
    async getArtifact(_slug, path) {
      return artifacts.get(path) ?? null;
    },
    ...overrides,
  };
}

const mimirService = {
  pages: {
    async listSources() {
      return [
        {
          id: 'src_1',
          title: 'Efficient Memory Management for Large Language Model Serving with PagedAttention',
          originType: 'arxiv' as const,
          originUrl: 'https://arxiv.org/abs/2309.06180',
          ingestedAt: now,
          ingestAgent: 'publisher',
          compiledInto: ['research/campaigns/local-model-serving/final.md'],
          content: 'PagedAttention benchmark excerpt for local serving.',
        },
        {
          id: 'src_2',
          title: 'SGLang: Efficient Execution of Structured Language Model Programs',
          originType: 'web' as const,
          originUrl: 'https://example.com/sglang',
          ingestedAt: now,
          ingestAgent: 'publisher',
          compiledInto: ['research/campaigns/local-model-serving/final.md'],
          content: 'Structured output serving excerpt.',
        },
      ];
    },
  },
};

const richMimirService = {
  pages: {
    async listSources() {
      return [
        {
          id: 'src_file',
          title: 'Cluster inventory snapshot',
          originType: 'file' as const,
          originPath: '/var/data/inventory.md',
          ingestedAt: now,
          ingestAgent: 'publisher',
          compiledInto: [],
          content: '',
        },
        {
          id: 'src_feed',
          title: 'Operator dispatch feed',
          originType: 'rss' as const,
          originUrl: 'https://ops.example.com/feed.xml',
          ingestedAt: now,
          ingestAgent: 'publisher',
          compiledInto: ['research/campaigns/durable-memory-proof/final.md'],
          content: 'Dispatch feed excerpt for research packaging.',
        },
        {
          id: 'src_chat',
          title: 'Operator chat summary',
          originType: 'chat' as const,
          ingestedAt: now,
          ingestAgent: 'publisher',
          compiledInto: ['research/campaigns/durable-memory-proof/final.md'],
          content: 'Chat summary about proof pack handling.',
        },
      ];
    },
  },
};

function makeDispatcherService(
  activityLog: Array<Record<string, unknown>> = [],
): IDispatcherService {
  const base = createMockDispatcherService();
  return {
    ...base,
    async getActivityLog(limit?: number) {
      const all = (
        activityLog.length > 0 ? activityLog : await base.getActivityLog(limit)
      ) as Awaited<ReturnType<IDispatcherService['getActivityLog']>>;
      return all.slice(0, limit ?? all.length ?? 100);
    },
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

describe('ResearchCampaignPage', () => {
  beforeEach(() => {
    mockNavigate.mockClear();
    mockSetTweak.mockClear();
    mockOpenEventStream.mockReset();
    mockOpenEventStream.mockReturnValue({ close: vi.fn() });
    mockSlug = 'local-model-serving';
    window.history.replaceState({}, '', '/');
    vi.stubGlobal(
      'confirm',
      vi.fn(() => true),
    );
  });

  it('renders the final synthesis state and opens source drawers', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(campaign, artifactDetails),
        'ting.dispatcher': createMockDispatcherService() as IDispatcherService,
        mimir: mimirService,
      }),
    });

    await waitFor(() =>
      expect(screen.getByText('Local model serving on the homelab')).toBeInTheDocument(),
    );
    expect(screen.getAllByText(/Final synthesis/i).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /open in Mímir/i })).toBeInTheDocument();
    expect(screen.getByText(/Evidence/i)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /\[s1\]/i }).length).toBeGreaterThan(0);

    fireEvent.click(screen.getAllByRole('button', { name: /\[s1\]/i })[0]!);
    await waitFor(() => expect(screen.getByText(/Source · \[s1\]/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'open' }));
    await waitFor(() => expect(screen.getByText(/Cited in/i)).toBeInTheDocument());
    expect(screen.getByText(/^arxiv\.org$/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /open external/i })).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', {
        name: /\[s2\]SGLang: Efficient Execution of Structured Language Model Programs/i,
      }),
    );
    await waitFor(() => expect(screen.getByText(/Source · \[s2\]/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /open external/i }));
    expect(openSpy).toHaveBeenCalledWith(
      'https://example.com/sglang',
      '_blank',
      'noopener,noreferrer',
    );

    fireEvent.click(
      screen.getByRole('button', {
        name: /research\/campaigns\/local-model-serving\/final\.md/i,
      }),
    );
    await waitFor(() =>
      expect(
        screen.getByText(/research\/campaigns\/local-model-serving\/final\.md/i),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /^critique\.md$/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/research\/campaigns\/local-model-serving\/critique\.md/i),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getAllByRole('button', { name: /open in Mímir/i })[0]!);
    expect(mockSetTweak).toHaveBeenCalledWith(
      'mimir.selectedPagePath',
      'research/campaigns/local-model-serving/final.md',
    );

    openSpy.mockRestore();
  });

  it('renders the running challenge state and opens notebook plus critique drawers', async () => {
    mockSlug = 'homelab-running';

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(runningCampaign, runningArtifactDetails),
        'ting.dispatcher': makeDispatcherService([
          {
            id: 'evt-running-1',
            event: 'workflow.campaign.updated',
            ownerId: 'lars',
            timestamp: now,
            data: {
              slug: 'homelab-running',
              persona: 'research-skeptic',
              summary: 'challenging throughput assumptions',
            },
          },
          {
            id: 'evt-running-2',
            event: 'workflow.campaign.updated',
            ownerId: 'lars',
            timestamp: now,
            data: {
              session_id: runningCampaign.sessionId,
              raven: 'publisher',
              message: 'queued publish follow-up',
            },
          },
        ]),
        mimir: mimirService,
      }),
    });

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { level: 1, name: 'Local model serving in progress' }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/Current stage/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Open notebook/i })).toBeInTheDocument();
    expect(screen.getAllByText(/research-skeptic/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/challenging throughput assumptions/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /2 sessions/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Open notebook/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/research\/campaigns\/homelab-running\/analysis\.md/i),
      ).toBeInTheDocument(),
    );
    expect(screen.getAllByText(/Tentative answer in one line/i).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /Throughput numbers are aspirational/i }));
    await waitFor(() => expect(screen.getByText(/Critique · \[c1\]/i)).toBeInTheDocument());
    expect(screen.getAllByText(/We still need measured benchmarks/i).length).toBeGreaterThan(0);

    fireEvent.click(
      screen.getByRole('button', { name: /research\/campaigns\/homelab-running\/critique\.md/i }),
    );
    await waitFor(() =>
      expect(
        screen.getByText(/research\/campaigns\/homelab-running\/critique\.md/i),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /Operator ▸/i }));
    await waitFor(() => expect(screen.getByText(/queued publish follow-up/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /^Run$/i }));
    await waitFor(() => expect(screen.getByText(/Run id/i)).toBeInTheDocument());
    expect(screen.getByText(runningCampaign.sessionId)).toBeInTheDocument();
    expect(screen.getByText(/^2$/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Actions$/i }));
    await waitFor(() => expect(screen.getByText(/Open final synthesis/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Open final synthesis/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/research\/campaigns\/homelab-running\/critique\.md/i),
      ).toBeInTheDocument(),
    );
  });

  it('renders the review-ready publish state and opens the manifest plus operator actions', async () => {
    mockSlug = 'skuld-fanout-options';

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(reviewCampaign, reviewArtifactDetails),
        'ting.dispatcher': createMockDispatcherService() as IDispatcherService,
        mimir: mimirService,
      }),
    });

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { level: 1, name: 'Replacing Skuld with a managed pub/sub?' }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /Publish to Mímir/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Send back for revision/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Annotated/i }));
    fireEvent.click(screen.getByRole('button', { name: /Publish to Mímir/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/research\/campaigns\/skuld-fanout-options\/manifest\.md/i),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /Send back for revision/i }));
    await waitFor(() => expect(screen.getByText(/Open in Völundr/i)).toBeInTheDocument());
    expect(screen.getByText(/Delete campaign/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Open manifest/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/research\/campaigns\/skuld-fanout-options\/manifest\.md/i),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /Operator ▸/i }));
    fireEvent.click(screen.getByRole('button', { name: /^Actions$/i }));
    fireEvent.click(screen.getByRole('button', { name: /Open final synthesis/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/research\/campaigns\/skuld-fanout-options\/final\.md/i),
      ).toBeInTheDocument(),
    );
  });

  it('renders the blocked state and can pivot into operator activity and actions', async () => {
    mockSlug = 'printer-rack-power';

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(blockedCampaign, new Map()),
        'ting.dispatcher': createMockDispatcherService() as IDispatcherService,
        mimir: mimirService,
      }),
    });

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { level: 1, name: 'Edge power budget for the printer rack' }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /Resume after fix/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /View blocking error/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /View blocking error/i }));
    await waitFor(() => expect(screen.getByText(/Activity/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Resume after fix/i }));
    await waitFor(() => expect(screen.getByText(/Open in Völundr/i)).toBeInTheDocument());
  });

  it('renders the failed state and can pivot between logs and run details', async () => {
    mockSlug = 'spark-throughput-failure';

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(failedCampaign, new Map()),
        'ting.dispatcher': createMockDispatcherService() as IDispatcherService,
        mimir: mimirService,
      }),
    });

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { level: 1, name: 'Spark cluster vs A6000 throughput' }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /Retry from stage/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /View run logs/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /View run logs/i }));
    await waitFor(() => expect(screen.getByText(/Activity/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /^Run$/i }));
    await waitFor(() => expect(screen.getByText(/Run id/i)).toBeInTheDocument());
    expect(screen.getByText(/valhalla/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Retry from stage/i }));
    await waitFor(() => expect(screen.getByText(/Open in Völundr/i)).toBeInTheDocument());
  });

  it('deletes a campaign after confirmation and routes back to the index', async () => {
    const deleteCampaign = vi.fn(async () => undefined);

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(campaign, artifactDetails, {
          deleteCampaign,
        }),
        'ting.dispatcher': createMockDispatcherService() as IDispatcherService,
        mimir: mimirService,
      }),
    });

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { level: 1, name: 'Local model serving on the homelab' }),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /^Delete$/i }));

    await waitFor(() => expect(deleteCampaign).toHaveBeenCalledWith(campaign.slug));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/research' });
  });

  it('does not delete a campaign when confirmation is cancelled', async () => {
    const deleteCampaign = vi.fn(async () => undefined);
    vi.stubGlobal(
      'confirm',
      vi.fn(() => false),
    );

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(campaign, artifactDetails, {
          deleteCampaign,
        }),
        'ting.dispatcher': makeDispatcherService(),
        mimir: mimirService,
      }),
    });

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { level: 1, name: 'Local model serving on the homelab' }),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /^Delete$/i }));

    await waitFor(() => expect(deleteCampaign).not.toHaveBeenCalled());
    expect(mockNavigate).not.toHaveBeenCalledWith({ to: '/ting/research' });
  });

  it('supports deep-linked drawers, fallback citations, and durable learnings', async () => {
    mockSlug = 'durable-memory-proof';
    window.history.replaceState({}, '', '/?drawer=sources&n=src_feed');

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(richPublishedCampaign, richArtifactDetails),
        'ting.dispatcher': createMockDispatcherService() as IDispatcherService,
        mimir: richMimirService,
      }),
    });

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { level: 1, name: 'Durable memory proof pack' }),
      ).toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.getByText(/3 sources cited/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /^Clean$/i }));
    expect(screen.getAllByRole('button', { name: /\[s1\]/i }).length).toBeGreaterThan(0);

    fireEvent.click(screen.getAllByRole('button', { name: /Open all in side panel →/i })[0]!);
    await waitFor(() =>
      expect(screen.getAllByText(/Operator dispatch feed/i).length).toBeGreaterThan(0),
    );
    expect(screen.getByText(/^ops\.example\.com$/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Files 4$/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/research\/campaigns\/durable-memory-proof\/final\.md/i),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: '×' }));
    await waitFor(() => expect(screen.queryByLabelText(/Research drawer/i)).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: /Learnings & follow-ups/i }));
    await waitFor(() => expect(screen.getByText(/Durable learnings/i)).toBeInTheDocument());
    expect(screen.getByText(/Open follow-ups/i)).toBeInTheDocument();

    const learningsHeading = screen.getByText(/Durable learnings/i);
    const learningsColumn = learningsHeading.closest('.ting-research-detail__lf-column');
    const learningsOpenButton = learningsColumn?.querySelector<HTMLButtonElement>(
      '.ting-research-detail__lf-action',
    );
    expect(learningsOpenButton).not.toBeNull();

    fireEvent.click(learningsOpenButton!);
    await waitFor(() =>
      expect(screen.getAllByText(/Durable packaging/i).length).toBeGreaterThan(0),
    );
    expect(
      screen.getAllByText(/One manifest should name the durable set\./i).length,
    ).toBeGreaterThan(1);

    fireEvent.click(
      screen.getByRole('button', {
        name: /Durable memory.*2 published · 0 review-ready · 1 notebook/i,
      }),
    );
    fireEvent.click(screen.getByText(/Local notebook only/i));
    fireEvent.click(
      screen.getByText(/followups\/research\/durable-memory-proof\.md/i).closest('button')!,
    );
    await waitFor(() =>
      expect(
        screen.getAllByText(/followups\/research\/durable-memory-proof\.md/i).length,
      ).toBeGreaterThan(0),
    );

    fireEvent.click(screen.getByRole('button', { name: /Evidence freshness is unclear/i }));
    await waitFor(() => expect(screen.getByText(/Critique · \[c1\]/i)).toBeInTheDocument());

    window.history.replaceState({}, '', '/');
  });

  it('renders the draft state and routes back into campaign creation', async () => {
    mockSlug = 'draft-idea';

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(draftCampaign, new Map()),
        'ting.dispatcher': createMockDispatcherService() as IDispatcherService,
        mimir: mimirService,
      }),
    });

    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1, name: 'Draft campaign' })).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /Complete brief → dispatch/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Discard draft/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Complete brief → dispatch/i }));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/research/new' });
  });

  it('supports critique drawer row selection and operator drawer actions', async () => {
    const deleteCampaign = vi.fn(async () => undefined);
    const originalLocation = window.location;
    const assign = vi.fn();
    Object.defineProperty(window, 'location', {
      value: { ...originalLocation, assign },
      writable: true,
    });

    mockSlug = 'homelab-running';

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(runningCampaign, runningArtifactDetails, {
          deleteCampaign,
        }),
        'ting.dispatcher': makeDispatcherService(),
        mimir: mimirService,
      }),
    });

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { level: 1, name: 'Local model serving in progress' }),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getAllByRole('button', { name: /Open all in side panel →/i })[1]!);
    await waitFor(() => expect(screen.getByText(/Critique · \[c1\]/i)).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole('button', { name: /Sglang case is undersold/i })[1]!);
    await waitFor(() => expect(screen.getByText(/Critique · \[c2\]/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Operator ▸/i }));
    fireEvent.click(screen.getByRole('button', { name: /^Actions$/i }));
    await waitFor(() => expect(screen.getByText(/Open in Völundr/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Open in Völundr/i }));
    expect(assign).toHaveBeenCalledWith(
      `/volundr/sessions/${encodeURIComponent(runningCampaign.sessionId)}`,
    );

    fireEvent.click(screen.getByRole('button', { name: /Delete campaign/i }));
    await waitFor(() => expect(deleteCampaign).toHaveBeenCalledWith(runningCampaign.slug));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/research' });

    Object.defineProperty(window, 'location', {
      value: originalLocation,
      writable: true,
    });
  });

  it('handles file-based sources without external links and falls back to the final artifact for Mímir open', async () => {
    mockSlug = 'durable-memory-proof';
    window.history.replaceState({}, '', '/?drawer=sources&n=src_file');

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(richPublishedCampaign, richArtifactDetails),
        'ting.dispatcher': createMockDispatcherService() as IDispatcherService,
        mimir: richMimirService,
      }),
    });

    await waitFor(() =>
      expect(screen.getAllByText(/Cluster inventory snapshot/i).length).toBeGreaterThan(0),
    );
    expect(screen.queryByRole('button', { name: /open external/i })).toBeNull();

    fireEvent.click(screen.getAllByRole('button', { name: /open in Mímir/i })[1]!);
    expect(mockSetTweak).toHaveBeenCalledWith(
      'mimir.selectedPagePath',
      'research/campaigns/durable-memory-proof/final.md',
    );
  });

  it('toggles evidence and skeptic sections and opens a source from the evidence table', async () => {
    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(campaign, artifactDetails),
        'ting.dispatcher': createMockDispatcherService() as IDispatcherService,
        mimir: mimirService,
      }),
    });

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { level: 1, name: 'Local model serving on the homelab' }),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /Evidence/i }));
    await waitFor(() =>
      expect(
        screen.getByRole('button', {
          name: /\[s1\]Efficient Memory Management for Large Language Model Serving with PagedAttention/i,
        }),
      ).toBeInTheDocument(),
    );

    fireEvent.click(
      screen.getByRole('button', {
        name: /\[s1\]Efficient Memory Management for Large Language Model Serving with PagedAttention/i,
      }),
    );
    await waitFor(() => expect(screen.getByLabelText(/Research drawer/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '×' }));
    await waitFor(() => expect(screen.queryByLabelText(/Research drawer/i)).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: /Skeptic's pass/i }));
    await waitFor(() =>
      expect(screen.queryByText(/Throughput numbers are aspirational/i)).toBeNull(),
    );

    fireEvent.click(screen.getByRole('button', { name: /Skeptic's pass/i }));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /Throughput numbers are aspirational/i }),
      ).toBeInTheDocument(),
    );
  });

  it('opens learnings, follow-ups, published memory, and review-ready memory cards', async () => {
    mockSlug = 'memory-review-ready';

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': makeResearchService(reviewMemoryCampaign, reviewMemoryArtifactDetails),
        'ting.dispatcher': createMockDispatcherService() as IDispatcherService,
        mimir: {
          pages: {
            async listSources() {
              return [];
            },
          },
        },
      }),
    });

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { level: 1, name: 'Memory review-ready branches' }),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /Learnings & follow-ups/i }));

    const learningsHeading = screen.getByText(/Durable learnings/i);
    const learningsColumn = learningsHeading.closest('.ting-research-detail__lf-column');
    const learningsOpenButton = learningsColumn?.querySelector<HTMLButtonElement>(
      '.ting-research-detail__lf-action',
    );
    expect(learningsOpenButton).not.toBeNull();

    fireEvent.click(learningsOpenButton!);
    await waitFor(() =>
      expect(screen.getByText(/learnings\/research\/memory-review-ready\.md/i)).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: '×' }));
    await waitFor(() => expect(screen.queryByLabelText(/Research drawer/i)).toBeNull());

    const followupsHeading = screen.getByText(/Open follow-ups/i);
    const followupsColumn = followupsHeading.closest('.ting-research-detail__lf-column');
    const followupsOpenButton = followupsColumn?.querySelectorAll<HTMLButtonElement>(
      '.ting-research-detail__lf-action',
    )[0];
    expect(followupsOpenButton).not.toBeNull();

    fireEvent.click(followupsOpenButton!);
    await waitFor(() =>
      expect(screen.getByText(/followups\/research\/memory-review-ready\.md/i)).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: '×' }));
    await waitFor(() => expect(screen.queryByLabelText(/Research drawer/i)).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: /Durable memory/i }));

    fireEvent.click(
      screen.getByRole('button', {
        name: /Final synthesisresearch\/campaigns\/memory-review-ready\/final\.md/i,
      }),
    );
    await waitFor(() =>
      expect(
        screen.getAllByText(/research\/campaigns\/memory-review-ready\/final\.md/i).length,
      ).toBeGreaterThan(0),
    );

    fireEvent.click(screen.getByRole('button', { name: '×' }));
    await waitFor(() => expect(screen.queryByLabelText(/Research drawer/i)).toBeNull());

    fireEvent.click(
      screen.getByRole('button', {
        name: /Manifestresearch\/campaigns\/memory-review-ready\/manifest\.md/i,
      }),
    );
    await waitFor(() =>
      expect(
        screen.getAllByText(/research\/campaigns\/memory-review-ready\/manifest\.md/i).length,
      ).toBeGreaterThan(0),
    );
  });

  it('shows loading and error states for slow or failed campaign fetches', async () => {
    const unresolvedResearch = makeResearchService(campaign, artifactDetails, {
      async getCampaign() {
        return new Promise<ResearchCampaignDetail | null>(() => {});
      },
    });

    const loadingView = render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': unresolvedResearch,
        'ting.dispatcher': createMockDispatcherService() as IDispatcherService,
        mimir: mimirService,
      }),
    });

    expect(screen.getByText(/Loading campaign…/i)).toBeInTheDocument();
    loadingView.unmount();

    const failingResearch = makeResearchService(campaign, artifactDetails, {
      async getCampaign() {
        throw new Error('boom');
      },
    });

    render(<ResearchCampaignPage />, {
      wrapper: wrap({
        'ting.research': failingResearch,
        'ting.dispatcher': createMockDispatcherService() as IDispatcherService,
        mimir: mimirService,
      }),
    });

    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
  });
});
