import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createMockValkyrieService } from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { useLearning, useReviseLearning, useSendLearningFeedback } from './useLearnings';

describe('useLearning', () => {
  it('loads one learning from the injected service', async () => {
    const { result } = renderHook(() => useLearning('learn-k8s-oom-canary'), {
      wrapper: wrapWithValkyrie(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.title).toBe('OOMKilled with rising queue depth');
  });

  it('stays idle without a learning id', () => {
    const { result } = renderHook(() => useLearning(null), { wrapper: wrapWithValkyrie() });
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('resolves null for an unknown learning (404)', async () => {
    const { result } = renderHook(() => useLearning('learn-ghost'), {
      wrapper: wrapWithValkyrie(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });
});

describe('useSendLearningFeedback', () => {
  it('records feedback and refreshes the learning cache', async () => {
    const service = createMockValkyrieService();
    const wrapper = wrapWithValkyrie({ valkyrie: service });

    const learning = renderHook(() => useLearning('learn-email-vendor-escalation'), { wrapper });
    await waitFor(() => expect(learning.result.current.isSuccess).toBe(true));
    expect(learning.result.current.data?.feedback).toBeUndefined();

    const feedback = renderHook(() => useSendLearningFeedback(), { wrapper });
    feedback.result.current.mutate({
      learningId: 'learn-email-vendor-escalation',
      verdict: 'useful',
      operatorId: 'human:operator',
    });
    await waitFor(() => expect(feedback.result.current.isSuccess).toBe(true));

    await waitFor(() => expect(learning.result.current.data?.feedback?.verdict).toBe('useful'));
  });

  it('surfaces the backend error for an invalid request', async () => {
    const wrapper = wrapWithValkyrie();
    const { result } = renderHook(() => useSendLearningFeedback(), { wrapper });

    result.current.mutate({
      learningId: 'learn-k8s-oom-canary',
      verdict: 'wrong_tier',
      operatorId: 'human:operator',
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as Error).message).toBe(
      'targetScope is required for wrong_tier feedback',
    );
  });
});

describe('useReviseLearning', () => {
  it('revises in place and reports an empty supersededId for candidates', async () => {
    const wrapper = wrapWithValkyrie();
    const { result } = renderHook(() => useReviseLearning(), { wrapper });

    result.current.mutate({
      learningId: 'learn-email-vendor-escalation',
      summary: 'tightened',
      reason: 'clean-up',
      operatorId: 'human:operator',
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.supersededId).toBe('');
    expect(result.current.data?.learning.summary).toBe('tightened');
  });

  it('creates a superseding candidate for an installed learning and caches it', async () => {
    const service = createMockValkyrieService();
    const wrapper = wrapWithValkyrie({ valkyrie: service });

    const revise = renderHook(() => useReviseLearning(), { wrapper });
    revise.result.current.mutate({
      learningId: 'learn-k8s-oom-canary',
      summary: 'narrowed window',
      reason: 'less noise',
      operatorId: 'human:operator',
    });
    await waitFor(() => expect(revise.result.current.isSuccess).toBe(true));
    expect(revise.result.current.data?.supersededId).toBe('learn-k8s-oom-canary');

    const successorId = revise.result.current.data!.learning.id;
    const successor = renderHook(() => useLearning(successorId), { wrapper });
    await waitFor(() => expect(successor.result.current.isSuccess).toBe(true));
    expect(successor.result.current.data?.supersedes).toBe('learn-k8s-oom-canary');
    expect(successor.result.current.data?.status).toBe('candidate');
  });
});
