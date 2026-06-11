import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { createMockValkyrieService, createSeedValkyrieDashboard } from '../adapters/mock';
import type { HuddleMessage, HuddleSummary } from '../domain';
import { VALKYRIE_DASHBOARD_QUERY_KEY, useValkyrieActions } from './useValkyrieDashboard';

function wrap(client: QueryClient, service = createMockValkyrieService()) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ valkyrie: service }}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('useValkyrieActions', () => {
  it('updates cached learning, huddle, message, and autonomy state', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const dashboard = createSeedValkyrieDashboard();
    const huddle = dashboard.huddles.find((entry) => entry.id === 'huddle-valhalla-now')!;
    const joinedHuddle: HuddleSummary = {
      ...huddle,
      joined: true,
      joinedParticipantId: 'human:jozef',
      joinedDisplayName: 'Jozef',
      joinedAction: 'teach',
    };
    const leftHuddle: HuddleSummary = {
      ...joinedHuddle,
      joined: false,
      joinedParticipantId: undefined,
      joinedDisplayName: undefined,
      joinedAction: undefined,
    };
    const message: HuddleMessage = {
      id: 'msg-hook-proof',
      huddleId: huddle.id,
      authorId: 'human:jozef',
      authorName: 'Jozef',
      body: 'per-user message',
      createdAt: '2026-06-03T14:12:00Z',
    };
    const adoptedLearning = { ...dashboard.learnings[0]!, status: 'adopted' as const };
    const autonomyDashboard = {
      ...dashboard,
      valkyries: dashboard.valkyries.map((entry) =>
        entry.id === 'valkyrie-valhalla-sigrun'
          ? { ...entry, autonomyMode: 'guarded' as const }
          : entry,
      ),
    };
    const service = {
      ...createMockValkyrieService(dashboard),
      adoptLearning: vi.fn().mockResolvedValue(adoptedLearning),
      joinHuddle: vi.fn().mockResolvedValue(joinedHuddle),
      leaveHuddle: vi.fn().mockResolvedValue(leftHuddle),
      sendHuddleMessage: vi.fn().mockResolvedValue(message),
      updateAutonomy: vi.fn().mockResolvedValue(autonomyDashboard),
    };
    client.setQueryData(VALKYRIE_DASHBOARD_QUERY_KEY, dashboard);

    const { result } = renderHook(() => useValkyrieActions(), {
      wrapper: wrap(client, service),
    });

    await act(() => result.current.adoptLearning.mutateAsync({ learningId: adoptedLearning.id }));
    expect(
      client.getQueryData<typeof dashboard>(VALKYRIE_DASHBOARD_QUERY_KEY)?.learnings[0],
    ).toEqual(adoptedLearning);

    await act(() =>
      result.current.joinHuddle.mutateAsync({
        huddleId: huddle.id,
        participantId: 'human:jozef',
        action: 'teach',
        targetFlockId: 'flock-k8s',
      }),
    );
    expect(
      client
        .getQueryData<typeof dashboard>(VALKYRIE_DASHBOARD_QUERY_KEY)
        ?.huddles.find((entry) => entry.id === huddle.id)?.joinedParticipantId,
    ).toBe('human:jozef');

    await act(() =>
      result.current.sendHuddleMessage.mutateAsync({
        huddleId: huddle.id,
        body: message.body,
        authorId: message.authorId,
      }),
    );
    await act(() =>
      result.current.sendHuddleMessage.mutateAsync({
        huddleId: huddle.id,
        body: message.body,
        authorId: message.authorId,
      }),
    );
    expect(
      client
        .getQueryData<typeof dashboard>(VALKYRIE_DASHBOARD_QUERY_KEY)
        ?.huddles.find((entry) => entry.id === huddle.id)
        ?.messages.filter((entry) => entry.id === message.id),
    ).toHaveLength(1);

    await act(() => result.current.leaveHuddle.mutateAsync(huddle.id));
    expect(
      client
        .getQueryData<typeof dashboard>(VALKYRIE_DASHBOARD_QUERY_KEY)
        ?.huddles.find((entry) => entry.id === huddle.id)?.joined,
    ).toBe(false);

    await act(() =>
      result.current.updateAutonomy.mutateAsync({
        valkyrieId: 'valkyrie-valhalla-sigrun',
        mode: 'guarded',
      }),
    );
    expect(client.getQueryData(VALKYRIE_DASHBOARD_QUERY_KEY)).toEqual(autonomyDashboard);
    await waitFor(() => expect(service.updateAutonomy).toHaveBeenCalled());
  });

  it('leaves an empty dashboard cache empty when huddle mutations succeed', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const huddle = createSeedValkyrieDashboard().huddles[0]!;
    const service = {
      ...createMockValkyrieService(),
      joinHuddle: vi.fn().mockResolvedValue(huddle),
      leaveHuddle: vi.fn().mockResolvedValue(huddle),
      sendHuddleMessage: vi.fn().mockResolvedValue({
        id: 'msg-no-cache',
        huddleId: huddle.id,
        authorId: 'human:jozef',
        authorName: 'Jozef',
        body: 'no cache',
        createdAt: '2026-06-03T14:13:00Z',
      } satisfies HuddleMessage),
    };
    const { result } = renderHook(() => useValkyrieActions(), {
      wrapper: wrap(client, service),
    });

    await act(() =>
      result.current.joinHuddle.mutateAsync({
        huddleId: huddle.id,
        participantId: 'human:jozef',
        action: 'observe',
      }),
    );
    await act(() => result.current.leaveHuddle.mutateAsync(huddle.id));
    await act(() =>
      result.current.sendHuddleMessage.mutateAsync({
        huddleId: huddle.id,
        body: 'no cache',
        authorId: 'human:jozef',
      }),
    );

    expect(client.getQueryData(VALKYRIE_DASHBOARD_QUERY_KEY)).toBeUndefined();
  });
});
