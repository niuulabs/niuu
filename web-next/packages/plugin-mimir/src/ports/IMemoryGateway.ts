import type { MemoryProposal, MemoryReview, MemorySearchResult } from '../domain/memoryGateway';

/** The service key the memory gateway's people routes are wired under. */
export const MEMORY_GATEWAY_SERVICE_KEY = 'mimir.memory';

export interface MemorySearchOptions {
  projectId?: string;
}

/**
 * The memory gateway's people routes, with the person's own login.
 *
 * Components call it via `useService<IMemoryGatewayService>(MEMORY_GATEWAY_SERVICE_KEY)`.
 */
export interface IMemoryGatewayService {
  /** One collated list of hits from every memory the person reads; one project's when named. */
  search(query: string, options?: MemorySearchOptions): Promise<MemorySearchResult>;
  /** What waits for the person: one layer's proposals, or every layer they decide. */
  review(layer?: string): Promise<MemoryReview>;
  keep(proposal: Pick<MemoryProposal, 'id' | 'layer'>): Promise<void>;
  discard(proposal: Pick<MemoryProposal, 'id' | 'layer'>, reason: string): Promise<void>;
}
