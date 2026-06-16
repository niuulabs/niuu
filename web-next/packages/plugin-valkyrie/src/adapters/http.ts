import type { ApiClient } from '@niuulabs/query';
import { normalizeReviewItem, type ReviewItem, type ValkyrieDashboard } from '../domain';
import type {
  AutonomyUpdateRequest,
  IOdinReviewService,
  IValkyrieService,
  ReviewDecisionRequest,
  ReviewListFilters,
} from '../ports';

export function buildValkyrieHttpAdapter(client: ApiClient): IValkyrieService {
  return {
    getDashboard() {
      return client.get<ValkyrieDashboard>('/dashboard');
    },
    updateAutonomy(request: AutonomyUpdateRequest) {
      return client.post<ValkyrieDashboard>('/autonomy', request);
    },
  };
}

export function buildOdinReviewHttpAdapter(client: ApiClient): IOdinReviewService {
  return {
    async listReviews(filters: ReviewListFilters = {}) {
      const params = new URLSearchParams();
      if (filters.status) params.set('status', filters.status);
      if (filters.kind) params.set('kind', filters.kind);
      if (filters.environmentId) params.set('environment_id', filters.environmentId);
      if (filters.limit !== undefined) params.set('limit', String(filters.limit));
      const query = params.toString();
      const rows = await client.get<Record<string, unknown>[]>(
        `/reviews${query ? `?${query}` : ''}`,
      );
      return rows.map(normalizeReviewItem);
    },
    async getReview(itemId: string) {
      try {
        const payload = await client.get<Record<string, unknown>>(
          `/reviews/${encodeURIComponent(itemId)}`,
        );
        return normalizeReviewItem(payload);
      } catch {
        return null;
      }
    },
    async decideReview(request: ReviewDecisionRequest): Promise<ReviewItem> {
      const response = await client.post<{ item: Record<string, unknown> }>(
        `/reviews/${encodeURIComponent(request.itemId)}/decide`,
        {
          decision: request.decision,
          reason: request.reason ?? '',
          participantId: request.participantId ?? '',
        },
      );
      return normalizeReviewItem(response.item);
    },
    getSummary() {
      return client.get('/reviews/summary');
    },
  };
}
