import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createMockValkyrieService, createSeedValkyrieDashboard } from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { useUpdateAutonomy, useValkyrieDashboard } from './useValkyrieDashboard';

describe('useValkyrieDashboard', () => {
  it('loads the dashboard from the injected service', async () => {
    const { result } = renderHook(() => useValkyrieDashboard(), {
      wrapper: wrapWithValkyrie(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.valkyries.length).toBeGreaterThan(0);
  });

  it('applies autonomy updates and refreshes the cache', async () => {
    const seed = createSeedValkyrieDashboard();
    const service = createMockValkyrieService(seed);
    const wrapper = wrapWithValkyrie({ valkyrie: service });

    const dashboard = renderHook(() => useValkyrieDashboard(), { wrapper });
    await waitFor(() => expect(dashboard.result.current.isSuccess).toBe(true));

    const update = renderHook(() => useUpdateAutonomy(), { wrapper });
    update.result.current.mutate({
      valkyrieId: seed.valkyries[0]!.id,
      mode: 'yolo',
      reason: 'test',
    });
    await waitFor(() => expect(update.result.current.isSuccess).toBe(true));
    expect(
      update.result.current.data?.valkyries.find((entry) => entry.id === seed.valkyries[0]!.id)
        ?.autonomyMode,
    ).toBe('yolo');
  });
});
