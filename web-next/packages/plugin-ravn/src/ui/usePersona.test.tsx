import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import {
  useCreatePersona,
  useForkPersona,
  usePersona,
  usePersonaYaml,
  useUpdatePersona,
} from './usePersona';
import { createMockPersonaStore } from '../adapters/mock';
import type { PersonaCreateRequest } from '../ports';

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

describe('useUpdatePersona', () => {
  it('updates a persona and invalidates cache', async () => {
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

    const { result } = renderHook(() => useUpdatePersona('coder'), { wrapper: Wrapper });

    const req: PersonaCreateRequest = {
      name: 'coder',
      role: 'build',
      letter: 'C',
      color: 'var(--color-accent-indigo)',
      summary: 'Updated',
      description: 'Updated description',
      systemPromptTemplate: '# coder',
      allowedTools: ['read'],
      forbiddenTools: [],
      permissionMode: 'default',
      iterationBudget: 40,
      llmPrimaryAlias: 'claude-opus-4-6',
      llmThinkingEnabled: true,
      llmMaxTokens: 16384,
      producesEventType: 'code.changed',
      producesSchema: {},
      consumesEvents: [],
      consumesSchema: {},
    };

    result.current.mutate(req);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(invalidateSpy).toHaveBeenCalled();
  });
});

describe('useCreatePersona', () => {
  it('creates a persona and invalidates cache', async () => {
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

    const { result } = renderHook(() => useCreatePersona(), { wrapper: Wrapper });

    result.current.mutate({
      name: 'streaming-reviewer',
      role: 'review',
      letter: 'S',
      color: 'var(--color-accent-indigo)',
      summary: 'Streaming reviewer',
      description: 'Streaming reviewer persona',
      systemPromptTemplate: '# streaming-reviewer',
      allowedTools: [],
      forbiddenTools: [],
      permissionMode: 'default',
      executor: {
        adapter: 'ravn.adapters.executors.cli.CliTransportExecutor',
        kwargs: {
          transport_adapter: 'skuld.transports.codex_ws.CodexWebSocketTransport',
        },
      },
      iterationBudget: 20,
      llmPrimaryAlias: 'claude-sonnet-4-6',
      llmThinkingEnabled: false,
      llmMaxTokens: 8192,
      producesEventType: '',
      producesSchema: {},
      consumesEvents: [],
      consumesSchema: {},
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(invalidateSpy).toHaveBeenCalled();
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
