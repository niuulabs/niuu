import type { ApiClient } from '@niuulabs/query';
import {
  normalizeReviewItem,
  type DecisionDetail,
  type DecisionRecord,
  type HistoryPage,
  type HuddleMessage,
  type HuddleSummary,
  type LearnedSkillRecord,
  type LearnedSkillSummary,
  type LearningRecord,
  type RealmSummary,
  type RealmTrustGrant,
  type ReviewItem,
  type SignalHistoryEntry,
  type SkillUsageStat,
  type TingWorkflowSummary,
  type ValkyrieDashboard,
} from '../domain';
import type {
  AutonomyUpdateRequest,
  DecisionListFilters,
  HuddleJoinInput,
  HuddleMessageInput,
  IOdinReviewService,
  IRealmGovernanceService,
  IValkyrieService,
  IValkyrieSkillsService,
  LearningFeedbackInput,
  LearningRevisionInput,
  LearningRevisionResult,
  ReviewDecisionRequest,
  ReviewListFilters,
  SignalHistoryFilters,
  TrustGrantCreate,
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
    joinHuddle(request: HuddleJoinInput) {
      return client.post<HuddleSummary>(`/huddles/${encodeURIComponent(request.huddleId)}/join`, {
        huddleId: request.huddleId,
        participantId: request.participantId,
        displayName: request.displayName ?? '',
        action: request.action ?? 'observe',
        targetFlockId: request.targetFlockId ?? '',
      });
    },
    leaveHuddle(huddleId: string) {
      return client.post<HuddleSummary>(`/huddles/${encodeURIComponent(huddleId)}/leave`, {});
    },
    sendHuddleMessage(request: HuddleMessageInput) {
      return client.post<HuddleMessage>(
        `/huddles/${encodeURIComponent(request.huddleId)}/messages`,
        {
          huddleId: request.huddleId,
          body: request.body,
          directedTo: request.directedTo ?? [],
          authorId: request.authorId,
        },
      );
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
    async getLearning(learningId: string) {
      try {
        return await client.get<LearningRecord>(`/learnings/${encodeURIComponent(learningId)}`);
      } catch {
        return null;
      }
    },
    sendLearningFeedback(request: LearningFeedbackInput) {
      return client.post<LearningRecord>(
        `/learnings/${encodeURIComponent(request.learningId)}/feedback`,
        {
          verdict: request.verdict,
          reason: request.reason ?? '',
          operatorId: request.operatorId,
          // Only wrong_tier carries a target scope; omit it otherwise so the
          // backend's optional-field validation never sees an empty string.
          ...(request.targetScope ? { targetScope: request.targetScope } : {}),
        },
      );
    },
    reviseLearning(request: LearningRevisionInput) {
      return client.post<LearningRevisionResult>(
        `/learnings/${encodeURIComponent(request.learningId)}/revise`,
        {
          ...(request.title !== undefined ? { title: request.title } : {}),
          ...(request.summary !== undefined ? { summary: request.summary } : {}),
          ...(request.content !== undefined ? { content: request.content } : {}),
          reason: request.reason,
          operatorId: request.operatorId,
        },
      );
    },
  };
}

/**
 * Learned skills live beside the dashboard API on the same Valkyrie base
 * (`<valkyrieBase>/skills`). Backend contract:
 * GET /skills?environment_id=… → { items, total }
 * GET /skills/{name}?environment_id=… → full record, 404 when unknown.
 */
export function buildValkyrieSkillsHttpAdapter(client: ApiClient): IValkyrieSkillsService {
  return {
    async listSkills(environmentId: string) {
      const response = await client.get<{ items: LearnedSkillSummary[]; total: number }>(
        `/skills${query({ environment_id: environmentId })}`,
      );
      return response.items;
    },
    async getSkill(environmentId: string, name: string) {
      try {
        return await client.get<LearnedSkillRecord>(
          `/skills/${encodeURIComponent(name)}${query({ environment_id: environmentId })}`,
        );
      } catch {
        return null;
      }
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

/**
 * Realm governance rides two backends: realms live on the shared niuu host
 * (`<sharedApiBase>/realms`), workflows on Ting (`<tingBase>/workflows`).
 * Pass one client scoped to each base.
 */
export function buildRealmGovernanceHttpAdapter(
  realmsClient: ApiClient,
  workflowsClient: ApiClient,
): IRealmGovernanceService {
  return {
    listRealms() {
      return realmsClient.get<RealmSummary[]>('/realms');
    },
    listTrustGrants(slug: string) {
      return realmsClient.get<RealmTrustGrant[]>(
        `/realms/${encodeURIComponent(slug)}/trust-grants`,
      );
    },
    createTrustGrant(slug: string, request: TrustGrantCreate) {
      return realmsClient.post<RealmTrustGrant>(
        `/realms/${encodeURIComponent(slug)}/trust-grants`,
        request,
      );
    },
    listWorkflows() {
      return workflowsClient.get<TingWorkflowSummary[]>('/workflows');
    },
  };
}
