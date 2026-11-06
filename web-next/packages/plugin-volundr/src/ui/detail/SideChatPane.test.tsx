import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { SideChatPane, sideChatLabel, sideChatName } from './SideChatPane';
import * as chatHooks from '../hooks/useSkuldChat';
import { SessionForkError } from '../../domain/session';
import type { IVolundrService } from '../../ports/IVolundrService';
import type { VolundrSession } from '../../models/volundr.model';

const PARENT: VolundrSession = {
  id: 'parent-1',
  name: 'forge-files-demo',
  source: { type: 'git', repo: 'niuulabs/niuu', branch: 'dev' },
  status: 'running',
  model: 'claude-sonnet-4-6',
  lastActive: 0,
  messageCount: 3,
  tokensUsed: 0,
  instanceId: 'valhalla',
};

const CHILD: VolundrSession = {
  ...PARENT,
  id: 'child-1',
  name: 'forge-files-demo-side',
  chatEndpoint: 'wss://forge.example/s/child-1/session',
};

function chatState(overrides: Partial<ReturnType<typeof chatHooks.useSkuldChat>> = {}) {
  return {
    messages: [],
    connected: true,
    historyLoaded: true,
    participants: new Map(),
    meshEvents: [],
    agentEvents: new Map(),
    pendingPermissions: [],
    pendingInputRequests: [],
    capabilities: {},
    sendMessage: vi.fn(),
    sendInterrupt: vi.fn(),
    respondToPermission: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof chatHooks.useSkuldChat>;
}

function renderPane(
  volundr: Partial<IVolundrService>,
  props: Partial<React.ComponentProps<typeof SideChatPane>> = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const handlers = { onForked: vi.fn(), onOpenSession: vi.fn(), onNewChat: vi.fn() };
  const view = render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ volundr: volundr as IVolundrService }}>
        <SideChatPane session={PARENT} childId={null} {...handlers} {...props} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { ...view, ...handlers };
}

afterEach(() => vi.restoreAllMocks());

describe('side chat naming', () => {
  it('reads as Lexi names it, and names the session as Forge accepts', () => {
    expect(sideChatLabel('forge-files-demo')).toBe('↳ forge-files-demo · side');
    expect(sideChatName('forge-files-demo')).toBe('forge-files-demo-side');
    expect(sideChatName('Fix the Bug!')).toBe('fix-the-bug-side');
    expect(sideChatName('')).toBe('session-side');
    const long = sideChatName(`${'a'.repeat(57)}-${'b'.repeat(20)}`);
    expect(long.length).toBeLessThanOrEqual(63);
    expect(long).toBe(`${'a'.repeat(57)}-side`);
  });
});

describe('SideChatPane', () => {
  it('forks the session into a named child on the same host', async () => {
    vi.spyOn(chatHooks, 'useSkuldChat').mockReturnValue(chatState());
    const forkSession = vi.fn().mockResolvedValue(CHILD);
    const { onForked } = renderPane({ forkSession });

    expect(screen.getByTestId('side-chat-pane')).toHaveTextContent('↳ forge-files-demo · side');
    fireEvent.click(screen.getByRole('button', { name: /Fork with context/ }));

    await waitFor(() => expect(onForked).toHaveBeenCalledWith('child-1'));
    expect(forkSession).toHaveBeenCalledWith('parent-1', 'forge-files-demo-side');
  });

  it('shows the child that was created when a later step failed, with what failed', async () => {
    vi.spyOn(chatHooks, 'useSkuldChat').mockReturnValue(chatState());
    const forkSession = vi
      .fn()
      .mockRejectedValue(new SessionForkError('child-1', 'Forge could not start it: no slot'));
    const { onForked } = renderPane({ forkSession });

    fireEvent.click(screen.getByRole('button', { name: /Fork with context/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('no slot');
    expect(onForked).toHaveBeenCalledWith('child-1');
  });

  it('says why a fork was refused outright', async () => {
    vi.spyOn(chatHooks, 'useSkuldChat').mockReturnValue(chatState());
    const forkSession = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('404'), { detail: 'No chronicle yet' }));
    const { onForked } = renderPane({ forkSession });

    fireEvent.click(screen.getByRole('button', { name: /Fork with context/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('No chronicle yet');
    expect(onForked).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /Fork with context/ })).toBeEnabled();
  });

  it('opens a new chat in the same workspace, or says why it cannot', () => {
    vi.spyOn(chatHooks, 'useSkuldChat').mockReturnValue(chatState());
    const { onNewChat, unmount } = renderPane({});
    fireEvent.click(screen.getByRole('button', { name: /New chat/ }));
    expect(onNewChat).toHaveBeenCalled();
    unmount();

    renderPane({}, { onNewChat: undefined });
    expect(screen.getByRole('button', { name: /New chat/ })).toBeDisabled();
  });

  it('offers nothing to start in a read-only session', () => {
    vi.spyOn(chatHooks, 'useSkuldChat').mockReturnValue(chatState());
    renderPane({}, { readOnly: true });
    expect(screen.getByRole('button', { name: /Fork with context/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /New chat/ })).toBeDisabled();
  });

  it("shows the child's conversation once it runs, and opens it as the main session", async () => {
    const useChat = vi.spyOn(chatHooks, 'useSkuldChat').mockReturnValue(
      chatState({
        messages: [
          { id: 'm1', role: 'user', content: 'Would paging break?', createdAt: new Date(0) },
        ],
      }),
    );
    const getSession = vi.fn().mockResolvedValue({ ...CHILD, status: 'running' });
    const { onOpenSession } = renderPane({ getSession }, { childId: 'child-1' });

    expect(await screen.findByText('Would paging break?')).toBeInTheDocument();
    expect(getSession).toHaveBeenCalledWith(
      'child-1',
      expect.objectContaining({ instanceId: 'valhalla' }),
    );
    expect(useChat).toHaveBeenLastCalledWith(
      'wss://forge.example/s/child-1/session',
      expect.anything(),
    );
    fireEvent.click(screen.getByRole('button', { name: /Open/ }));
    expect(onOpenSession).toHaveBeenCalledWith('child-1');
  });

  it('says the child is starting, has stopped, has failed or cannot be read', async () => {
    vi.spyOn(chatHooks, 'useSkuldChat').mockReturnValue(chatState());
    const cases: Array<[Partial<VolundrSession> | Error, string]> = [
      [{ status: 'starting', chatEndpoint: undefined }, 'The side chat is starting…'],
      [{ status: 'stopped' }, 'The side chat has stopped.'],
      [{ status: 'failed', error: 'image pull' }, 'The side chat failed: image pull'],
      [new Error('gone'), 'gone'],
    ];
    for (const [answer, text] of cases) {
      const getSession =
        answer instanceof Error
          ? vi.fn().mockRejectedValue(answer)
          : vi.fn().mockResolvedValue({ ...CHILD, ...answer });
      const { unmount } = renderPane({ getSession }, { childId: 'child-1' });
      expect(await screen.findByText(new RegExp(text.replace('…', '.')))).toBeInTheDocument();
      unmount();
    }
  });
});
