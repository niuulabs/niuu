import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import {
  useForkPersona,
  usePersona,
  usePersonaDocument,
  usePersonaYaml,
  usePersonaYamlCheck,
  useSavePersonaYaml,
} from './usePersona';
import { createMockPersonaStore } from '../adapters/mock';

function createWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ 'ravn.personas': createMockPersonaStore() }}>
          {children}
        </ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('usePersona', () => {
  it('fetches a persona by name', async () => {
    const { result } = renderHook(() => usePersona('reviewer'), {
      wrapper: createWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.name).toBe('reviewer');
    expect(result.current.data?.role).toBe('review');
  });

  it('is disabled when name is empty', () => {
    const { result } = renderHook(() => usePersona(''), {
      wrapper: createWrapper(),
    });
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('returns an error for an unknown persona', async () => {
    const { result } = renderHook(() => usePersona('nonexistent-persona'), {
      wrapper: createWrapper(),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
  });
});

describe('usePersonaYaml', () => {
  it('fetches YAML for a persona', async () => {
    const { result } = renderHook(() => usePersonaYaml('coder'), {
      wrapper: createWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toContain('coder');
  });
});

describe('usePersonaDocument', () => {
  it('fetches the persona document', async () => {
    const { result } = renderHook(() => usePersonaDocument('coder'), {
      wrapper: createWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.name).toBe('coder');
  });
});

describe('usePersonaYamlCheck', () => {
  it('asks the store whether YAML can be saved, and not for empty YAML', async () => {
    const { result } = renderHook(() => usePersonaYamlCheck('description: no name\n', true), {
      wrapper: createWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.valid).toBe(false);
    const idle = renderHook(() => usePersonaYamlCheck('   ', true), { wrapper: createWrapper() });
    expect(idle.result.current.fetchStatus).toBe('idle');
  });
});

describe('useSavePersonaYaml', () => {
  function wrapperWith(store: ReturnType<typeof createMockPersonaStore>, client: QueryClient) {
    return function Wrapper({ children }: { children: React.ReactNode }) {
      return (
        <QueryClientProvider client={client}>
          <ServicesProvider services={{ 'ravn.personas': store }}>{children}</ServicesProvider>
        </QueryClientProvider>
      );
    };
  }

  it('creates a persona from YAML and invalidates the cache', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const store = createMockPersonaStore();
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries');
    const { result } = renderHook(() => useSavePersonaYaml(null), {
      wrapper: wrapperWith(store, client),
    });

    result.current.mutate('name: streaming-reviewer\n');

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.name).toBe('streaming-reviewer');
    expect(invalidateSpy).toHaveBeenCalled();
  });

  it('replaces the named persona with its YAML', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const store = createMockPersonaStore();
    const { result } = renderHook(() => useSavePersonaYaml('coder'), {
      wrapper: wrapperWith(store, client),
    });

    result.current.mutate('name: coder\ndescription: Writes careful code.\n');

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.description).toBe('Writes careful code.');
  });
});

describe('useForkPersona', () => {
  it('forks a persona and invalidates cache', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const store = createMockPersonaStore();
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries');

    function Wrapper({ children }: { children: React.ReactNode }) {
      return (
        <QueryClientProvider client={client}>
          <ServicesProvider services={{ 'ravn.personas': store }}>{children}</ServicesProvider>
        </QueryClientProvider>
      );
    }

    const { result } = renderHook(() => useForkPersona('reviewer'), { wrapper: Wrapper });

    result.current.mutate('reviewer-copy');
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(invalidateSpy).toHaveBeenCalled();
  });
});
