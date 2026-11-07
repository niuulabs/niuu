import { useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { SessionsPage } from './SessionsPage';
import type { RoomWorkerFocus } from './LiveSessionDetailPage';
import {
  createMockPtyStream,
  createMockFileSystemPort,
  createMockVolundrService,
} from '../adapters/mock';
import type { ISessionStore } from '../ports/ISessionStore';
import type { Session } from '../domain/session';

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useParams: () => ({ sessionId: 'lead' }),
}));

const resizeCallbacks: ResizeObserverCallback[] = [];
vi.stubGlobal(
  'ResizeObserver',
  class {
    constructor(callback: ResizeObserverCallback) {
      resizeCallbacks.push(callback);
    }
    observe = vi.fn();
    disconnect = vi.fn();
    unobserve = vi.fn();
  },
);

/** Resize the page to `width`, as a window resize would. */
function resizeTo(width: number) {
  act(() => {
    for (const callback of resizeCallbacks) {
      callback([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver);
    }
  });
}

// The open session's chat opens and closes its dock; the list makes room for it.
vi.mock('./LiveSessionDetailPage', () => ({
  LiveSessionDetailPage: ({ onDockOpenChange }: RoomWorkerFocus) => {
    const [open, setOpen] = useState(false);
    return (
      <button
        type="button"
        onClick={() => {
          setOpen(!open);
          onDockOpenChange?.(!open);
        }}
      >
        Toggle dock
      </button>
    );
  },
}));

const session: Session = {
  id: 'lead',
  name: 'storm-probe',
  ravnId: 'ravn-lead',
  personaName: 'storm-probe',
  templateId: 'tpl',
  clusterId: 'cluster-a',
  state: 'running',
  startedAt: '2026-11-05T05:00:00.000Z',
  lastActivityAt: '2026-11-05T05:05:00.000Z',
  resources: {
    cpuRequest: 1,
    cpuLimit: 1,
    cpuUsed: 0,
    memRequestMi: 1,
    memLimitMi: 1,
    memUsedMi: 0,
    gpuCount: 0,
  },
  env: {},
  events: [],
};

function renderPage() {
  const store: ISessionStore = {
    getSession: async (id) => (id === session.id ? session : null),
    listSessions: async () => [session],
    createSession: async () => {
      throw new Error('not used');
    },
    updateSession: async () => {
      throw new Error('not used');
    },
    deleteSession: async () => {},
    subscribe: () => () => {},
  };
  const volundr = createMockVolundrService();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          bifrost: createMockBifrostService(),
          volundr,
          'niuu.repos': { getRepos: volundr.getRepos.bind(volundr) },
          sessionStore: store,
          ptyStream: createMockPtyStream(),
          filesystem: createMockFileSystemPort(),
        }}
      >
        <SessionsPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

const folded = () => screen.queryByRole('button', { name: 'Expand pods sidebar' }) !== null;

describe('SessionsPage — room for the dock', () => {
  beforeEach(() => {
    resizeCallbacks.length = 0;
  });

  it('folds the list when the dock has no room, and unfolds it when the dock closes', async () => {
    renderPage();
    const toggle = await screen.findByRole('button', { name: 'Toggle dock' });
    // 1100px holds the 340px list and the 480px conversation, not the dock's 340 as well.
    resizeTo(1100);
    expect(folded()).toBe(false);

    fireEvent.click(toggle);
    expect(folded()).toBe(true);
    expect(screen.queryByRole('separator', { name: 'Resize session list' })).toBeNull();

    fireEvent.click(toggle);
    expect(folded()).toBe(false);
  });

  it('leaves the list open when the window has room for all three', async () => {
    renderPage();
    const toggle = await screen.findByRole('button', { name: 'Toggle dock' });
    resizeTo(1400);
    fireEvent.click(toggle);
    expect(folded()).toBe(false);

    resizeTo(1000);
    expect(folded()).toBe(true);
    resizeTo(1400);
    expect(folded()).toBe(false);
  });

  it('keeps the list open once the person expands it, until the dock no longer needs it', async () => {
    renderPage();
    const toggle = await screen.findByRole('button', { name: 'Toggle dock' });
    resizeTo(1100);
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole('button', { name: 'Expand pods sidebar' }));
    expect(folded()).toBe(false);

    // Closing the dock ends that choice: the next open folds the list again.
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(folded()).toBe(true);

    // A list the person collapsed stays collapsed after the dock closes.
    fireEvent.click(screen.getByRole('button', { name: 'Expand pods sidebar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Collapse pods sidebar' }));
    fireEvent.click(toggle);
    expect(folded()).toBe(true);
  });
});
