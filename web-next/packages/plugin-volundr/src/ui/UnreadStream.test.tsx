import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { UnreadStream, latestReply } from './UnreadStream';
import { createMockVolundrService } from '../adapters/mock';
import { renderWithVolundr } from '../testing/renderWithVolundr';
import type { Session } from '../domain/session';
import type { IVolundrService, VolundrConversationTurn } from '../ports/IVolundrService';
import type { SessionReadState } from '../models/volundr.model';

const NOW = Date.parse('2026-11-05T12:00:00Z');
const minutesAgo = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

type Observed = { target: Element; fire: () => void };
const observed: Observed[] = [];
vi.stubGlobal(
  'IntersectionObserver',
  class {
    constructor(private readonly callback: IntersectionObserverCallback) {}
    observe(target: Element) {
      observed.push({
        target,
        fire: () =>
          this.callback(
            [{ isIntersecting: true, target } as IntersectionObserverEntry],
            this as unknown as IntersectionObserver,
          ),
      });
    }
    disconnect() {}
  },
);
function scrollIntoView(testId: string) {
  const entry = observed.find((item) => (item.target as HTMLElement).dataset.testid === testId);
  if (!entry) throw new Error(`${testId} is not observed`);
  act(() => entry.fire());
}
function showEverything() {
  act(() => observed.forEach((entry) => entry.fire()));
}

function state(overrides: Partial<SessionReadState> = {}): SessionReadState {
  return {
    isUnread: true,
    latestOutputSeq: 14,
    latestOutputTurnId: 'turn-1',
    latestOutputAt: minutesAgo(3),
    readThroughSeq: 9,
    manuallyUnread: false,
    revision: 3,
    ...overrides,
  };
}

function session(id: string, overrides: Partial<Session> = {}): Session {
  return {
    id,
    name: `name-${id}`,
    ravnId: id,
    personaName: id,
    templateId: 't',
    clusterId: 'thor',
    state: 'idle',
    startedAt: minutesAgo(600),
    resources: {
      cpuRequest: 0,
      cpuLimit: 0,
      cpuUsed: 0,
      memRequestMi: 0,
      memLimitMi: 0,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [],
    readState: state(),
    ...overrides,
  };
}

const reply = (id: string, content: string, extra: Partial<VolundrConversationTurn> = {}) => ({
  id,
  role: 'assistant',
  content,
  created_at: minutesAgo(3),
  ...extra,
});

function setup(
  sessions: Session[],
  service: Partial<IVolundrService> = {},
  onOpen: (id: string) => void = vi.fn(),
) {
  const volundr = {
    ...createMockVolundrService(),
    getConversationHistory: vi.fn(async () => ({
      turns: [reply('turn-1', 'Done. **All green.**')],
    })),
    changeSessionReadState: vi.fn(async () => state({ isUnread: false })),
    ...service,
  } as IVolundrService;
  renderWithVolundr(<UnreadStream sessions={sessions} onOpen={onOpen} />, { service: volundr });
  return { volundr, onOpen };
}

beforeEach(() => {
  observed.length = 0;
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
});
afterEach(() => {
  vi.useRealTimers();
});

describe('latestReply', () => {
  const turns = [
    reply('t1', 'first'),
    reply('t2', 'internal note', { visibility: 'internal' }),
    { id: 't3', role: 'user', content: 'question', created_at: minutesAgo(2) },
  ];
  it('finds the reply the read state names', () => {
    expect(latestReply(turns, 't1').content).toBe('first');
  });
  it('takes the newest public assistant reply when none is named', () => {
    expect(latestReply(turns, null).content).toBe('first');
  });
  it('fails when the named reply is missing rather than showing another', () => {
    expect(() => latestReply(turns, 'gone')).toThrow('(gone) is not in this session');
  });
  it('fails when there is no assistant reply at all', () => {
    expect(() => latestReply([turns[2]!], undefined)).toThrow('no assistant reply');
  });
});

describe('UnreadStream', () => {
  it('summarises the unread sessions and shows each reply with who, state and how long ago', async () => {
    setup([session('a', { state: 'awaiting_input' }), session('b')]);
    expect(screen.getByTestId('unread-stream-summary')).toHaveTextContent(
      '2 sessions have new replies',
    );
    showEverything();

    const block = screen.getByTestId('unread-block-a');
    expect(within(block).getByText('name-a')).toBeInTheDocument();
    expect(within(block).getByText('Needs you')).toBeInTheDocument();
    expect(within(block).getByText('3m ago')).toBeInTheDocument();
    expect(await within(block).findByText(/All green/)).toBeInTheDocument();
  });

  it('says so when the host marked a session unread by hand', () => {
    setup([session('a', { readState: state({ manuallyUnread: true }) })]);
    expect(screen.getByText('Marked unread')).toBeInTheDocument();
  });

  it('fetches a reply only for the sessions that reach the screen', async () => {
    const { volundr } = setup([session('a'), session('b')]);
    expect(volundr.getConversationHistory).not.toHaveBeenCalled();

    scrollIntoView('unread-block-b');
    await waitFor(() => expect(volundr.getConversationHistory).toHaveBeenCalledTimes(1));
    expect(volundr.getConversationHistory).toHaveBeenCalledWith('b');
  });

  it('shows a loading state while the reply is fetched', () => {
    setup([session('a')], { getConversationHistory: () => new Promise(() => {}) });
    showEverything();
    expect(screen.getByRole('status', { name: 'Loading latest reply…' })).toBeInTheDocument();
  });

  it('shows the failure and fetches again on retry', async () => {
    const getConversationHistory = vi
      .fn()
      .mockRejectedValueOnce(new Error('Host unreachable'))
      .mockResolvedValue({ turns: [reply('turn-1', 'Recovered')] });
    setup([session('a')], { getConversationHistory });
    showEverything();

    expect(await screen.findByText('Could not load the latest reply')).toBeInTheDocument();
    expect(screen.getByText('Host unreachable')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Recovered')).toBeInTheDocument();
  });

  it('reports a reply that the history does not hold', async () => {
    setup([session('a')], {
      getConversationHistory: async () => ({ turns: [reply('other', 'x')] }),
    });
    showEverything();
    expect(await screen.findByText(/\(turn-1\) is not in this session/)).toBeInTheDocument();
  });

  it('opens the session', () => {
    const { onOpen } = setup([session('a')]);
    fireEvent.click(screen.getByRole('button', { name: 'Open session' }));
    expect(onOpen).toHaveBeenCalledWith('a');
  });

  it('marks one session read with the revision and output it saw', async () => {
    const { volundr } = setup([session('a')]);
    fireEvent.click(screen.getByRole('button', { name: 'Mark name-a read' }));
    await waitFor(() =>
      expect(volundr.changeSessionReadState).toHaveBeenCalledExactlyOnceWith('a', {
        state: 'read',
        expectedRevision: 3,
        throughSeq: 14,
      }),
    );
  });

  it('marks every session read at once', async () => {
    const { volundr } = setup([session('a'), session('b')]);
    fireEvent.click(screen.getByRole('button', { name: 'Mark all read' }));
    await waitFor(() => expect(volundr.changeSessionReadState).toHaveBeenCalledTimes(2));
  });

  it('shows what failed when marking read does not go through', async () => {
    setup([session('a'), session('b')], {
      changeSessionReadState: vi.fn(async () => {
        throw new Error('Forge is down');
      }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Mark all read' }));
    expect(await screen.findByTestId('unread-stream-error')).toHaveTextContent(
      '2 of 2 sessions could not be marked read: Forge is down',
    );
  });

  it('says everything is read when nothing is unread', () => {
    setup([]);
    expect(screen.getByTestId('unread-stream-summary')).toHaveTextContent('No unread replies');
    expect(screen.getByText('All caught up')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Mark all read' })).toBeDisabled();
  });

  it('counts one session in the singular', () => {
    setup([session('a')]);
    expect(screen.getByTestId('unread-stream-summary')).toHaveTextContent(
      '1 session has new replies',
    );
  });
});
