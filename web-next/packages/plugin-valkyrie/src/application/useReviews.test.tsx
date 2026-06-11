import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createMockOdinReviewService } from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { useDecideReview, useReviewList, useReviewSummary } from './useReviews';

describe('useReviews hooks', () => {
  it('lists reviews through the injected service', async () => {
    const { result } = renderHook(() => useReviewList({ status: 'pending' }), {
      wrapper: wrapWithValkyrie(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.every((item) => item.status === 'pending')).toBe(true);
  });

  it('summarises the queue', async () => {
    const { result } = renderHook(() => useReviewSummary(), { wrapper: wrapWithValkyrie() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.pendingTotal).toBeGreaterThan(0);
  });

  it('decides an item and refreshes the queue', async () => {
    const service = createMockOdinReviewService();
    const wrapper = wrapWithValkyrie({ 'valkyrie.reviews': service });
    const list = renderHook(() => useReviewList({ status: 'pending' }), { wrapper });
    await waitFor(() => expect(list.result.current.isSuccess).toBe(true));
    const target = list.result.current.data?.[0];
    expect(target).toBeDefined();

    const decide = renderHook(() => useDecideReview(), { wrapper });
    decide.result.current.mutate({
      itemId: target!.itemId,
      decision: 'approved',
      reason: 'fine',
    });
    await waitFor(() => expect(decide.result.current.isSuccess).toBe(true));
    expect(decide.result.current.data?.status).toBe('applied');
  });

  it('surfaces decision failures', async () => {
    const wrapper = wrapWithValkyrie();
    const decide = renderHook(() => useDecideReview(), { wrapper });
    decide.result.current.mutate({ itemId: 'review:none', decision: 'approved' });
    await waitFor(() => expect(decide.result.current.isError).toBe(true));
  });
});
