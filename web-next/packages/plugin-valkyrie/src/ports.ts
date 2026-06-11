import type {
  AutonomyMode,
  ReviewItem,
  ReviewKind,
  ReviewStatus,
  ReviewSummary,
  ValkyrieDashboard,
} from './domain';

export interface AutonomyUpdateRequest {
  valkyrieId: string;
  mode: AutonomyMode;
  reason: string;
  participantId?: string;
}

export interface IValkyrieService {
  getDashboard(): Promise<ValkyrieDashboard>;
  updateAutonomy(request: AutonomyUpdateRequest): Promise<ValkyrieDashboard>;
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
