import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useDispatchQueue } from './useDispatchQueue';

const mockQueue = [
  {
    saga_id: 's-1',
    saga_name: 'Alpha',
    saga_slug: 'alpha',
    repos: ['org/repo'],
    feature_branch: 'feat/alpha',
    phase_name: 'Phase 1',
    issue_id: 'i-1',
    identifier: 'NIU-100',
    title: 'Fix bug',
    description: 'Fix it',
    status: 'Todo',
    priority: 1,
    priority_label: 'Urgent',
    estimate: 2,
    url: 'https://example.com/i-1',
  },
];

const mockConfig = {
  default_system_prompt: 'You are helpful.',
  default_model: 'claude-sonnet-4-6',
  models: [{ id: 'claude-sonnet-4-6', name: 'Claude Sonnet' }],
};

const mockClusters = [
  { connection_id: 'c-1', name: 'prod', url: 'https://prod.example.com', enabled: true },
];

describe('useDispatchQueue', () => {
  beforeEach(() => {
    vi.spyOn(global, 'fetch').mockImplementation(async input => {
      const url = typeof input === 'string' ? input : (input as Request).url;
      if (url.includes('/queue')) {
        return { ok: true, status: 200, json: async () => mockQueue } as Response;
      }
      if (url.includes('/config')) {
        return { ok: true, status: 200, json: async () => mockConfig } as Response;
      }
      if (url.includes('/clusters')) {
        return { ok: true, status: 200, json: async () => mockClusters } as Response;
      }
      if (url.includes('/session-definitions')) {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (url.includes('/approve')) {
        return {
          ok: true,
          status: 200,
          json: async () => [
            {
              issue_id: 'i-1',
              session_id: 's-1',
              session_name: 'test',
              status: 'spawned',
              cluster_name: 'prod',
            },
          ],
        } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('should fetch queue, config, and clusters on mount', async () => {
    const { result } = renderHook(() => useDispatchQueue());
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.queue).toHaveLength(1);
    expect(result.current.defaults.default_model).toBe('claude-sonnet-4-6');
    expect(result.current.clusters).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });

  it('should handle fetch error', async () => {
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network error'));
    const { result } = renderHook(() => useDispatchQueue());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('Network error');
  });

  it('should handle non-Error rejection', async () => {
    vi.spyOn(global, 'fetch').mockRejectedValue('boom');
    const { result } = renderHook(() => useDispatchQueue());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('boom');
  });

  it('should dispatch items and remove them from queue', async () => {
    const { result } = renderHook(() => useDispatchQueue());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let results: unknown;
    await act(async () => {
      results = await result.current.dispatch(
        [{ saga_id: 's-1', issue_id: 'i-1', repo: 'org/repo' }],
        'claude-sonnet-4-6',
        'prompt'
      );
    });
    expect(results).toHaveLength(1);
    expect(result.current.queue).toHaveLength(0);
  });

  it('should dispatch with connectionId', async () => {
    const { result } = renderHook(() => useDispatchQueue());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.dispatch(
        [{ saga_id: 's-1', issue_id: 'i-1', repo: 'org/repo', connection_id: 'c-1' }],
        'claude-sonnet-4-6',
        'prompt',
        'c-1'
      );
    });
    expect(result.current.dispatching).toBe(false);
  });
});
