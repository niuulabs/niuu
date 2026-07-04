import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import {
  useDecisionDetail,
  useDecisionList,
  useSignalHistory,
  useSkillStats,
} from './useValkyrieHistory';

describe('useValkyrieHistory hooks', () => {
  it('lists decisions filtered by environment', async () => {
    const { result } = renderHook(
      () => useDecisionList({ environmentId: 'env-k8s-valhalla', limit: 8 }),
      { wrapper: wrapWithValkyrie() },
    );
    await waitFor(() => expect(result.current.data).toBeDefined());
    // 6 seed decisions live on env-k8s-valhalla (incl. the 3 PV re-judgments).
    expect(result.current.data!.total).toBe(6);
    expect(result.current.data!.items[0]!.environmentId).toBe('env-k8s-valhalla');
  });

  it('fetches decision detail lineage only when an id is set', async () => {
    const { result } = renderHook(() => useDecisionDetail('decision-oom-1'), {
      wrapper: wrapWithValkyrie(),
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data!.decision.decisionId).toBe('decision-oom-1');
    expect(result.current.data!.lineage.signals[0]!.signalId).toBe('sig-hist-oom');

    const disabled = renderHook(() => useDecisionDetail(null), {
      wrapper: wrapWithValkyrie(),
    });
    expect(disabled.result.current.fetchStatus).toBe('idle');
  });

  it('pages signal history with severity filters', async () => {
    const { result } = renderHook(
      () => useSignalHistory({ environmentId: 'env-k8s-valhalla', severity: 'critical' }),
      { wrapper: wrapWithValkyrie() },
    );
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data!.items).toHaveLength(1);
    expect(result.current.data!.items[0]!.severity).toBe('critical');
  });

  it('fetches learned-skill usage stats', async () => {
    const { result } = renderHook(() => useSkillStats('env-k8s-valhalla'), {
      wrapper: wrapWithValkyrie(),
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data!.map((entry) => entry.skillName)).toContain(
      'k8s_memory_pressure_probe',
    );
  });

  it('surfaces service failures', async () => {
    const broken = {
      listDecisions: () => Promise.reject(new Error('history offline')),
    };
    const { result } = renderHook(() => useDecisionList({}), {
      wrapper: wrapWithValkyrie({ valkyrie: broken }),
    });
    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error));
  });
});
