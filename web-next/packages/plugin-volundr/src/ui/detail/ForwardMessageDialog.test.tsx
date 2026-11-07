import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ForwardMessageDialog, forwardTargets, forwardedText } from './ForwardMessageDialog';
import type { Session } from '../../domain/session';

function makeSession(overrides: Partial<Session> & Pick<Session, 'id'>): Session {
  return {
    ravnId: 'ravn',
    personaName: 'persona',
    templateId: 'tpl',
    clusterId: 'thor',
    state: 'running',
    startedAt: '2026-11-05T08:00:00Z',
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
    ...overrides,
  };
}

const ROWS: Session[] = [
  makeSession({ id: 'current', name: 'This one', model: 'claude-sonnet-4-5' }),
  makeSession({
    id: 'alpha',
    name: 'Alpha build',
    model: 'claude-sonnet-4-5',
    clusterName: 'Thor',
    state: 'idle',
  }),
  makeSession({ id: 'beta', name: 'Beta review', model: 'gpt-5', clusterName: 'Odin' }),
  makeSession({ id: 'gone', name: 'Stopped one', state: 'terminated' }),
  makeSession({ id: 'booting', name: 'Booting one', state: 'provisioning' }),
];

function setup({
  sendMessage = vi.fn().mockResolvedValue({
    status: 'delivered',
    requestId: 'req-1',
    sessionId: 'alpha',
  }),
  rows = ROWS,
}: { sendMessage?: ReturnType<typeof vi.fn>; rows?: Session[] } = {}) {
  const onDelivered = vi.fn();
  const onOpenChange = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          volundr: { sendMessage },
          sessionStore: {
            listSessions: vi.fn().mockResolvedValue(rows),
            subscribe: () => () => {},
          },
        }}
      >
        <ForwardMessageDialog
          open
          onOpenChange={onOpenChange}
          currentSessionId="current"
          currentSessionName="This one"
          content="Ship it on Friday."
          onDelivered={onDelivered}
        />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { sendMessage, onDelivered, onOpenChange };
}

describe('forwardedText', () => {
  it('heads the message with the session it came from', () => {
    expect(forwardedText('This one', 'Hello')).toBe('Forwarded from This one:\n\nHello');
  });
});

describe('forwardTargets', () => {
  it('keeps running sessions other than the current one', () => {
    expect(forwardTargets(ROWS, 'current', '').map((s) => s.id)).toEqual(['alpha', 'beta']);
  });

  it.each([
    ['name', 'alpha', ['alpha']],
    ['model label', 'gpt', ['beta']],
    ['host', 'odin', ['beta']],
    ['state label', 'idle', ['alpha']],
    ['nothing', 'zzz', []],
  ])('filters by %s', (_what, query, ids) => {
    expect(forwardTargets(ROWS, 'current', query).map((s) => s.id)).toEqual(ids);
  });
});

describe('ForwardMessageDialog', () => {
  it('lists other running sessions with model, host and state, never the current one', async () => {
    setup();
    const list = await screen.findByTestId('forward-target-list');
    expect(list).toHaveTextContent('Alpha build');
    expect(list).toHaveTextContent('Beta review');
    expect(list).toHaveTextContent('Thor');
    expect(list).toHaveTextContent('Idle');
    expect(list).not.toHaveTextContent('This one');
    expect(list).not.toHaveTextContent('Stopped one');
    expect(list).not.toHaveTextContent('Booting one');
    expect(screen.getByRole('button', { name: 'Forward' })).toBeDisabled();
  });

  it('filters the list from the filter box and says when nothing matches', async () => {
    setup();
    await screen.findByTestId('forward-target-list');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'beta' } });
    expect(screen.queryByText('Alpha build')).not.toBeInTheDocument();
    expect(screen.getByText('Beta review')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'zzz' } });
    expect(screen.getByText('No sessions match')).toBeInTheDocument();
  });

  it('says so when there is no other running session', async () => {
    setup({ rows: [ROWS[0]!] });
    expect(await screen.findByText('No other running sessions')).toBeInTheDocument();
  });

  it('sends the headed text to the picked session and reports delivery', async () => {
    const { sendMessage, onDelivered } = setup();
    fireEvent.click(await screen.findByRole('radio', { name: /Alpha build/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Forward' }));
    await waitFor(() => expect(onDelivered).toHaveBeenCalledWith({ targetName: 'Alpha build' }));
    expect(sendMessage).toHaveBeenCalledExactlyOnceWith(
      'alpha',
      'Forwarded from This one:\n\nShip it on Friday.',
    );
  });

  it('never calls an accepted-but-unconfirmed message delivered', async () => {
    const sendMessage = vi
      .fn()
      .mockResolvedValue({ status: 'pending', requestId: 'req-2', sessionId: 'beta' });
    const { onDelivered } = setup({ sendMessage });
    fireEvent.click(await screen.findByRole('radio', { name: /Beta review/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Forward' }));
    const status = await screen.findByTestId('forward-pending');
    expect(status).toHaveTextContent('still delivering');
    expect(status).toHaveTextContent('not confirmed');
    expect(onDelivered).not.toHaveBeenCalled();
    // The same message cannot be sent a second time from here.
    expect(screen.queryByRole('button', { name: 'Forward' })).not.toBeInTheDocument();
    expect(
      within(screen.getByTestId('forward-message-dialog')).getByRole('button', { name: 'Close' }),
    ).toBeInTheDocument();
  });

  it('keeps the dialog open with the server sentence when forwarding fails', async () => {
    const refusal = Object.assign(new Error('API request failed: 404'), {
      detail: 'Session beta has no active endpoint',
    });
    const sendMessage = vi.fn().mockRejectedValue(refusal);
    const { onDelivered, onOpenChange } = setup({ sendMessage });
    fireEvent.click(await screen.findByRole('radio', { name: /Beta review/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Forward' }));
    expect(await screen.findByTestId('forward-failed')).toHaveTextContent(
      'Session beta has no active endpoint',
    );
    expect(onDelivered).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Forward' })).toBeEnabled();
  });

  it('cancels without sending', async () => {
    const { sendMessage, onOpenChange } = setup();
    await screen.findByTestId('forward-target-list');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(sendMessage).not.toHaveBeenCalled();
  });
});
