import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { usePersonas } from './usePersonas';
import type { PersonaSummary } from '../ports';
import { wrapWithServices } from '../testing/wrapWithRavn';

const samplePersona: PersonaSummary = {
  name: 'coder',
  permissionMode: 'workspace-write',
  allowedTools: ['file', 'git'],
  iterationBudget: 40,
  isBuiltin: true,
  hasOverride: false,
  producesEvent: 'code.changed',
  consumesEvents: ['code.requested'],
};

const makeWrapper = wrapWithServices;

describe('usePersonas', () => {
  it('returns personas list from the service', async () => {
    const svc = { listPersonas: vi.fn().mockResolvedValue([samplePersona]) };

    const { result } = renderHook(() => usePersonas(), {
      wrapper: makeWrapper({ 'ravn.personas': svc }),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data?.[0]?.name).toBe('coder');
    expect(svc.listPersonas).toHaveBeenCalled();
  });

  it('enters error state when service rejects', async () => {
    const svc = {
      listPersonas: vi.fn().mockRejectedValue(new Error('service down')),
    };

    const { result } = renderHook(() => usePersonas(), {
      wrapper: makeWrapper({ 'ravn.personas': svc }),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
  });

  it('starts in loading state', () => {
    const svc = {
      listPersonas: vi.fn().mockReturnValue(new Promise(() => undefined)),
    };

    const { result } = renderHook(() => usePersonas(), {
      wrapper: makeWrapper({ 'ravn.personas': svc }),
    });

    expect(result.current.isLoading).toBe(true);
  });
});
