import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type {
  IOdinReviewService,
  ReviewDecisionRequest,
  ReviewListFilters,
  ReviewSummaryFilters,
} from '../ports';

export const REVIEWS_QUERY_KEY = ['valkyrie', 'reviews'] as const;

export function useReviewList(filters: ReviewListFilters = {}) {
  const service = useService<IOdinReviewService>('valkyrie.reviews');
  return useQuery({
    queryKey: [...REVIEWS_QUERY_KEY, 'list', filters],
    queryFn: () => service.listReviews(filters),
    refetchInterval: 10_000,
  });
}

/** One review by id, for a link that names it (it may be on no loaded page). */
export function useReview(itemId: string) {
  const service = useService<IOdinReviewService>('valkyrie.reviews');
  return useQuery({
    queryKey: [...REVIEWS_QUERY_KEY, 'item', itemId],
    queryFn: () => service.getReview(itemId),
    enabled: itemId !== '',
  });
}

export function useReviewSummary(filters: ReviewSummaryFilters = {}) {
  const service = useService<IOdinReviewService>('valkyrie.reviews');
  return useQuery({
    queryKey: [...REVIEWS_QUERY_KEY, 'summary', filters],
    queryFn: () => service.getSummary(filters),
    refetchInterval: 10_000,
  });
}

export function useDecideReview() {
  const service = useService<IOdinReviewService>('valkyrie.reviews');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: ReviewDecisionRequest) => service.decideReview(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: REVIEWS_QUERY_KEY });
    },
  });
}
