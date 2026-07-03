import type { ApiClient } from '@niuulabs/query';
import {
  normalizeReviewItem,
  type DecisionDetail,
  type DecisionRecord,
  type HistoryPage,
  type ReviewItem,
  type SignalHistoryEntry,
  type SkillUsageStat,
  type ValkyrieDashboard,
} from '../domain';
import type {
  AutonomyUpdateRequest,
  DecisionListFilters,
  IOdinReviewService,
  IValkyrieService,
  ReviewDecisionRequest,
  ReviewListFilters,
  SignalHistoryFilters,
} from '../ports';

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : '';
}

export function buildValkyrieHttpAdapter(client: ApiClient): IValkyrieService {
  return {
    getDashboard() {
      return client.get<ValkyrieDashboard>('/dashboard');
    },
    updateAutonomy(request: AutonomyUpdateRequest) {
      return client.post<ValkyrieDashboard>('/autonomy', request);
    },
    listDecisions(filters: DecisionListFilters = {}) {
      return client.get<HistoryPage<DecisionRecord>>(
        `/decisions${query({
          environment_id: filters.environmentId,
          valkyrie_id: filters.valkyrieId,
          operational_state: filters.operationalState,
          limit: filters.limit,
          offset: filters.offset,
        })}`,
      );
    },
    async getDecision(decisionId: string) {
      try {
        return await client.get<DecisionDetail>(`/decisions/${encodeURIComponent(decisionId)}`);
      } catch {
        return null;
      }
    },
    listSignalHistory(filters: SignalHistoryFilters = {}) {
      return client.get<HistoryPage<SignalHistoryEntry>>(
        `/signals/history${query({
          environment_id: filters.environmentId,
          severity: filters.severity,
          limit: filters.limit,
          offset: filters.offset,
        })}`,
      );
    },
    async getSkillStats(environmentId?: string) {
      const response = await client.get<{ skills: SkillUsageStat[] }>(
        `/learnings/stats/skills${query({ environment_id: environmentId })}`,
      );
      return response.skills;
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
