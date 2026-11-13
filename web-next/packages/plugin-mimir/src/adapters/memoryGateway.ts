/**
 * HTTP and mock adapters for the memory gateway's people routes
 * (`{mimir}/memory/search`, `/memory/review`, `/memory/proposals/{id}/approve|reject`).
 */
import type { ApiClient } from '@niuulabs/query';
import type {
  MemoryHit,
  MemoryProposal,
  MemorySearchResult,
  ReviewSource,
} from '../domain/memoryGateway';
import type { IMemoryGatewayService } from '../ports/IMemoryGateway';

interface RawHit {
  layer: string;
  layer_name: string;
  kind: MemoryHit['kind'];
  ref: string;
  title: string;
  snippet: string;
}

interface RawSearch {
  query: string;
  hits: RawHit[];
}

interface RawProposal {
  id: string;
  layer: string;
  layer_name: string;
  title: string;
  now: string | null;
  would: string;
  why: string | null;
  by: 'agent' | 'person';
  created_at: string;
  sources: ReviewSource[];
}

interface RawReview {
  proposals: RawProposal[];
  more: string[];
}

function toSearchResult(raw: RawSearch): MemorySearchResult {
  return {
    query: raw.query,
    hits: raw.hits.map((hit) => ({
      layer: hit.layer,
      layerName: hit.layer_name,
      kind: hit.kind,
      ref: hit.ref,
      title: hit.title,
      snippet: hit.snippet,
    })),
  };
}

function toProposal(raw: RawProposal): MemoryProposal {
  return {
    id: raw.id,
    layer: raw.layer,
    layerName: raw.layer_name,
    title: raw.title,
    now: raw.now,
    would: raw.would,
    why: raw.why,
    by: raw.by,
    createdAt: raw.created_at,
    sources: raw.sources,
  };
}

export function buildMemoryGatewayHttpAdapter(client: ApiClient): IMemoryGatewayService {
  return {
    async search(query, options = {}) {
      const params = new URLSearchParams({ q: query });
      if (options.projectId) params.set('project_id', options.projectId);
      const raw = await client.get<RawSearch>(`/memory/search?${params}`);
      return toSearchResult(raw);
    },
    async review(layer) {
      const query = layer ? `?${new URLSearchParams({ layer })}` : '';
      const raw = await client.get<RawReview>(`/memory/review${query}`);
      return { proposals: raw.proposals.map(toProposal), more: raw.more };
    },
    async keep(proposal) {
      await client.post(`/memory/proposals/${encodeURIComponent(proposal.id)}/approve`, {
        layer: proposal.layer,
      });
    },
    async discard(proposal, reason) {
      await client.post(`/memory/proposals/${encodeURIComponent(proposal.id)}/reject`, {
        layer: proposal.layer,
        reason,
      });
    },
  };
}

const MOCK_PROJECT = 'project:booking';

function mockProposals(): MemoryProposal[] {
  return [
    {
      id: 'p-1',
      layer: MOCK_PROJECT,
      layerName: 'Booking platform',
      title: 'The booking cut-over date',
      now: 'The cut-over is the weekend of 5 December.',
      would: 'The cut-over is the weekend of 12 December.',
      why: 'The payments freeze moved a week.',
      by: 'agent',
      createdAt: '2026-11-11T09:00:00Z',
      sources: [
        {
          ref: 'url:https://linear.app/acme/issue/LIN-501',
          kind: 'url',
          title: null,
          url: 'https://linear.app/acme/issue/LIN-501',
          kept: true,
        },
      ],
    },
    {
      id: 'p-2',
      layer: MOCK_PROJECT,
      layerName: 'Booking platform',
      title: 'Who decides the cut-over',
      now: null,
      would: 'Ines Moreau owns the cut-over and makes the go/no-go call.',
      why: 'Ines said so in the planning thread.',
      by: 'agent',
      createdAt: '2026-11-11T10:00:00Z',
      sources: [{ ref: 'tool_call:abc', kind: 'tool_call', title: null, url: null, kept: true }],
    },
  ];
}

const MOCK_HITS: MemoryHit[] = [
  {
    layer: MOCK_PROJECT,
    layerName: 'Booking platform',
    kind: 'page',
    ref: 'projects/booking-cutover',
    title: 'Booking cut-over',
    snippet: 'The cut-over is planned for the weekend of 12 December.',
  },
  {
    layer: 'org',
    layerName: 'the org',
    kind: 'source',
    ref: 'source:src_cutover_thread',
    title: 'Cut-over planning thread',
    snippet: 'Ines Moreau owns the cut-over and makes the go/no-go call.',
  },
];

/** A gateway that answers from fixed records, for demos and tests. */
export function createMemoryGatewayMockAdapter(): IMemoryGatewayService {
  let waiting = mockProposals();
  return {
    async search(query, options = {}) {
      const needle = query.trim().toLowerCase();
      if (!needle) throw new Error('query is required');
      const hits = MOCK_HITS.filter(
        (hit) =>
          (!options.projectId || hit.layer === `project:${options.projectId}`) &&
          `${hit.title} ${hit.snippet}`.toLowerCase().includes(needle),
      );
      return { query, hits };
    },
    async review(layer) {
      return {
        proposals: waiting.filter((proposal) => !layer || proposal.layer === layer),
        more: [],
      };
    },
    async keep(proposal) {
      waiting = waiting.filter((item) => item.id !== proposal.id);
    },
    async discard(proposal) {
      waiting = waiting.filter((item) => item.id !== proposal.id);
    },
  };
}
