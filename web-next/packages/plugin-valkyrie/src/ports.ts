import type {
  AutonomyMode,
  DecisionDetail,
  DecisionRecord,
  HistoryPage,
  RealmSummary,
  RealmTrustGrant,
  ReviewItem,
  ReviewKind,
  ReviewStatus,
  ReviewSummary,
  SignalHistoryEntry,
  SkillUsageStat,
  TingWorkflowSummary,
  ValkyrieDashboard,
} from './domain';

export interface AutonomyUpdateRequest {
  valkyrieId: string;
  mode: AutonomyMode;
  reason: string;
  participantId?: string;
}

export interface DecisionListFilters {
  environmentId?: string;
  valkyrieId?: string;
  operationalState?: string;
  limit?: number;
  offset?: number;
}

export interface SignalHistoryFilters {
  environmentId?: string;
  severity?: string;
  limit?: number;
  offset?: number;
}

export interface IValkyrieService {
  getDashboard(): Promise<ValkyrieDashboard>;
  updateAutonomy(request: AutonomyUpdateRequest): Promise<ValkyrieDashboard>;
  listDecisions(filters?: DecisionListFilters): Promise<HistoryPage<DecisionRecord>>;
  getDecision(decisionId: string): Promise<DecisionDetail | null>;
  listSignalHistory(filters?: SignalHistoryFilters): Promise<HistoryPage<SignalHistoryEntry>>;
  getSkillStats(environmentId?: string): Promise<SkillUsageStat[]>;
}

export interface ReviewListFilters {
  status?: ReviewStatus | '';
  kind?: ReviewKind | '';
  environmentId?: string;
  limit?: number;
}

export interface ReviewDecisionRequest {
  itemId: string;
  decision: 'approved' | 'rejected';
  reason?: string;
  participantId?: string;
}

export interface IOdinReviewService {
  listReviews(filters?: ReviewListFilters): Promise<ReviewItem[]>;
  getReview(itemId: string): Promise<ReviewItem | null>;
  decideReview(request: ReviewDecisionRequest): Promise<ReviewItem>;
  getSummary(): Promise<ReviewSummary>;
}

/** Body for POST /api/v1/realms/{slug}/trust-grants (exact backend casing). */
export interface TrustGrantCreate {
  action_class: string;
  target: string;
  level: number;
  limits: Record<string, unknown>;
  granted_by?: string | null;
}

export interface IRealmGovernanceService {
  listRealms(): Promise<RealmSummary[]>;
  listTrustGrants(slug: string): Promise<RealmTrustGrant[]>;
  createTrustGrant(slug: string, request: TrustGrantCreate): Promise<RealmTrustGrant>;
  listWorkflows(): Promise<TingWorkflowSummary[]>;
}
