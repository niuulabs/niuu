/**
 * HTTP and mock adapters for the memory gateway's people routes
 * (`{mimir}/memory/ask`, `/memory/review`, `/memory/proposals/{id}/approve|reject`).
 */
import type { ApiClient } from '@niuulabs/query';
import type {
  MemoryAnswer,
  MemoryProposal,
  MemorySource,
  ReviewSource,
} from '../domain/memoryGateway';
import type { IMemoryGatewayService } from '../ports/IMemoryGateway';

interface RawSource {
  layer: string;
  layer_name: string;
  ref: string;
  kind: MemorySource['kind'];
  title: string;
  url: string | null;
}

interface RawAnswer {
  answer: string;
  gaps: string[];
  sources: RawSource[];
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

function toAnswer(raw: RawAnswer): MemoryAnswer {
  return {
    answer: raw.answer,
    gaps: raw.gaps,
    sources: raw.sources.map((source) => ({
      layer: source.layer,
      layerName: source.layer_name,
      ref: source.ref,
      kind: source.kind,
      title: source.title,
      url: source.url,
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
    async ask(question, projectId) {
      const raw = await client.post<RawAnswer>('/memory/ask', {
        question,
        ...(projectId ? { project_id: projectId } : {}),
      });
      return toAnswer(raw);
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

/** A gateway that answers from fixed records, for demos and tests. */
export function createMemoryGatewayMockAdapter(): IMemoryGatewayService {
  let waiting = mockProposals();
  return {
    async ask(question) {
      if (!question.trim()) throw new Error('question is required');
      return {
        answer:
          'The cut-over is planned for the weekend of 12 December. Ines Moreau owns it and makes the go/no-go call.',
        gaps: [
          'Not settled: the date moved from 5 December last week and nobody has confirmed it.',
        ],
        sources: [
          {
            layer: MOCK_PROJECT,
            layerName: 'Booking platform',
            ref: 'decisions/booking-cutover',
            kind: 'page',
            title: 'Booking cut-over',
            url: null,
          },
        ],
      };
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
