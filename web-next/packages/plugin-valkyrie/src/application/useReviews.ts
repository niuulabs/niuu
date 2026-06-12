import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IOdinReviewService, ReviewDecisionRequest, ReviewListFilters } from '../ports';

export const REVIEWS_QUERY_KEY = ['valkyrie', 'reviews'] as const;

export function useReviewList(filters: ReviewListFilters = {}) {
  const service = useService<IOdinReviewService>('valkyrie.reviews');
  return useQuery({
    queryKey: [...REVIEWS_QUERY_KEY, 'list', filters],
    queryFn: () => service.listReviews(filters),
    refetchInterval: 10_000,
  });
}

export function useReviewSummary() {
  const service = useService<IOdinReviewService>('valkyrie.reviews');
  return useQuery({
    queryKey: [...REVIEWS_QUERY_KEY, 'summary'],
    queryFn: () => service.getSummary(),
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
