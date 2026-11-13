import type { MemoryAnswer, MemoryProposal, MemoryReview } from '../domain/memoryGateway';

/** The service key the memory gateway's people routes are wired under. */
export const MEMORY_GATEWAY_SERVICE_KEY = 'mimir.memory';

/**
 * The memory gateway's people routes, with the person's own login.
 *
 * Components call it via `useService<IMemoryGatewayService>(MEMORY_GATEWAY_SERVICE_KEY)`.
 */
export interface IMemoryGatewayService {
  /** A cited answer over the layers the person reads; one project's when named. */
  ask(question: string, projectId?: string): Promise<MemoryAnswer>;
  /** What waits for the person: one layer's proposals, or every layer they decide. */
  review(layer?: string): Promise<MemoryReview>;
  keep(proposal: Pick<MemoryProposal, 'id' | 'layer'>): Promise<void>;
  discard(proposal: Pick<MemoryProposal, 'id' | 'layer'>, reason: string): Promise<void>;
}
