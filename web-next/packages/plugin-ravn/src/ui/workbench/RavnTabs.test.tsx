import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { mountRavn, ravnServices } from '../../testing/mountRavn';
import { RAVN_ID, SESSION_ID, failedRavn, makeRavn, makeSession } from '../../testing/fixtures';

vi.mock('./LiveChat', () => ({
  LiveChat: ({ chatEndpoint }: { chatEndpoint: string }) => (
    <div data-testid="live-chat">{chatEndpoint}</div>
  ),
}));

beforeEach(() => localStorage.clear());

const CONVERSATION_CAPS = [
  'chat',
  'session.list',
  'session.create',
  'session.delete',
  'runtime.restart',
  'logs',
] as const;

const other = '44444444-4444-4444-8444-444444444444';

describe('Chat tab', () => {
  it('talks to a running resident on its own endpoint', async () => {
    mountRavn(
      '/ravn',
      ravnServices({ ravens: [makeRavn({ chatEndpoint: 'ws://localhost/s/r1/session' })] }),
    );
    expect(await screen.findByTestId('live-chat')).toHaveTextContent(/\/s\/r1\/session$/);
  });

  it('lists conversations for runtimes that hold several, live one first', async () => {
    const services = ravnServices({
      ravens: [makeRavn({ capabilities: [...CONVERSATION_CAPS] })],
      residentSessions: [
        makeSession({ id: other, status: 'idle', title: 'Nightly audit' }),
        makeSession({ title: 'Kernel flake', chatEndpoint: 'ws://localhost/s/k/session' }),
      ],
    });
    const router = mountRavn('/ravn', services);
    expect(await screen.findByTestId('live-chat')).toHaveTextContent(/\/s\/k\/session$/);
    const conversations = screen.getAllByTestId('ravn-conversation');
    expect(conversations[0]).toHaveTextContent('Kernel flake');
    expect(conversations[0]).toHaveTextContent('● live');

    fireEvent.click(screen.getByText('Nightly audit'));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ session: other }));
    expect(await screen.findByText(/This conversation is idle/)).toBeInTheDocument();
  });

  it('starts a new conversation and opens it', async () => {
    const services = ravnServices({
      ravens: [makeRavn({ capabilities: [...CONVERSATION_CAPS] })],
    });
    services['ravn.residents'].createSession = vi
      .fn()
      .mockResolvedValue(makeSession({ id: other, title: 'Spark check' }));
    const router = mountRavn('/ravn', services);
    expect(await screen.findByText('No conversations yet')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('ravn-conversation-new'));
    const dialog = await screen.findByRole('dialog', { name: 'New conversation' });
    fireEvent.change(within(dialog).getByTestId('ravn-conversation-title'), {
      target: { value: 'Spark check' },
    });
    fireEvent.click(within(dialog).getByTestId('ravn-conversation-create'));
    await waitFor(() =>
      expect(services['ravn.residents'].createSession).toHaveBeenCalledWith(
        expect.objectContaining({ id: RAVN_ID }),
        { title: 'Spark check' },
      ),
    );
    await waitFor(() => expect(router.state.location.search).toMatchObject({ session: other }));
  });

  it('shows why a conversation could not be started', async () => {
    const services = ravnServices({ ravens: [makeRavn({ capabilities: [...CONVERSATION_CAPS] })] });
    services['ravn.residents'].createSession = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'engine busy' }));
    mountRavn('/ravn', services);
    fireEvent.click(await screen.findByTestId('ravn-conversation-new'));
    fireEvent.change(await screen.findByTestId('ravn-conversation-title'), {
      target: { value: 'Try' },
    });
    fireEvent.click(screen.getByTestId('ravn-conversation-create'));
    expect(await screen.findByText('engine busy')).toBeInTheDocument();
  });

  it('closes a conversation after confirmation', async () => {
    const services = ravnServices({
      ravens: [makeRavn({ capabilities: [...CONVERSATION_CAPS] })],
      residentSessions: [makeSession({ title: 'Kernel flake' })],
    });
    mountRavn('/ravn', services);
    fireEvent.click(await screen.findByRole('button', { name: 'Close Kernel flake' }));
    const dialog = await screen.findByRole('dialog', { name: 'Close conversation' });
    fireEvent.click(within(dialog).getByTestId('ravn-conversation-close-confirm'));
    await waitFor(() =>
      expect(services['ravn.residents'].deleteSession).toHaveBeenCalledWith(
        expect.objectContaining({ id: RAVN_ID }),
        SESSION_ID,
      ),
    );
  });

  it('says why a failed ravn cannot talk and offers the fix', async () => {
    const services = ravnServices({ ravens: [failedRavn()] });
    const router = mountRavn('/ravn', services);
    const empty = await screen.findByTestId('ravn-chat-unavailable');
    expect(empty).toHaveTextContent("Muninn isn't running");
    fireEvent.click(within(empty).getByRole('button', { name: /Restart/ }));
    await waitFor(() =>
      expect(services['ravn.residents'].applyLifecycle).toHaveBeenCalledWith(
        expect.anything(),
        'restart',
      ),
    );
    fireEvent.click(within(empty).getByRole('button', { name: 'View logs' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'activity' }));
  });

  it('offers resume on a suspended ravn', async () => {
    const services = ravnServices({
      ravens: [makeRavn({ observedState: 'suspended', desiredState: 'suspended' })],
    });
    mountRavn('/ravn', services);
    const empty = await screen.findByTestId('ravn-chat-unavailable');
    fireEvent.click(within(empty).getByRole('button', { name: /Resume/ }));
    await waitFor(() =>
      expect(services['ravn.residents'].applyLifecycle).toHaveBeenCalledWith(
        expect.anything(),
        'resume',
      ),
    );
  });

  it('reads the transcript of an ended conversation', async () => {
    const ravn = makeRavn({ managed: false, kind: 'persona', observedState: undefined });
    const services = ravnServices({
      ravens: [ravn],
      sessions: [makeSession({ status: 'stopped', chatEndpoint: null })],
    });
    services['ravn.sessions'].getMessages = vi.fn().mockResolvedValue([
      {
        id: '55555555-5555-4555-8555-555555555555',
        sessionId: SESSION_ID,
        kind: 'user',
        content: 'status please',
        ts: '2026-09-24T08:00:00Z',
      },
    ]);
    mountRavn('/ravn', services);
    expect(await screen.findByText('status please')).toBeInTheDocument();
    expect(screen.getByText(/This conversation is stopped/)).toBeInTheDocument();
  });

  it('shows why a transcript could not be read', async () => {
    const services = ravnServices({
      ravens: [makeRavn({ managed: false, kind: 'persona', observedState: undefined })],
      sessions: [makeSession({ status: 'stopped', chatEndpoint: null })],
    });
    services['ravn.sessions'].getMessages = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'transcript gone' }));
    mountRavn('/ravn', services);
    expect(await screen.findByText('transcript gone')).toBeInTheDocument();
  });
});

describe('Activity tab', () => {
  it('shows the runtime log and filters it', async () => {
    mountRavn(`/ravn?ravn=${RAVN_ID}&tab=activity`, ravnServices());
    const logs = await screen.findByRole('log', { name: 'Runtime logs' });
    expect(within(logs).getByText('turn started')).toBeInTheDocument();
    expect(screen.getByText('2 of 2 lines')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Errors' }));
    expect(within(logs).queryByText('turn started')).not.toBeInTheDocument();
    expect(within(logs).getByText('tool failed')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    fireEvent.change(screen.getByLabelText('Search logs'), { target: { value: 'nothing' } });
    expect(screen.getByText('No log lines match.')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('ravn-logs-follow'));
    expect(screen.getByTestId('ravn-logs-follow')).toHaveTextContent('Follow');
    fireEvent.click(screen.getByRole('button', { name: 'Refresh logs' }));
  });

  it('says when logs cannot be read', async () => {
    const services = ravnServices({ ravens: [failedRavn()] });
    services['ravn.residents'].getLogs = vi
      .fn()
      .mockRejectedValue(
        Object.assign(new Error('x'), { detail: '{"detail":"container engine unreachable"}' }),
      );
    mountRavn(`/ravn?ravn=${RAVN_ID}&tab=activity`, services);
    expect(await screen.findByTestId('ravn-logs-error')).toHaveTextContent(
      'container engine unreachable',
    );
    expect(screen.queryByTestId('ravn-logs-follow')).not.toBeInTheDocument();
  });

  it('says when the runtime reported nothing', async () => {
    const services = ravnServices();
    services['ravn.residents'].getLogs = vi.fn().mockResolvedValue({ entries: [], bufferTotal: 0 });
    mountRavn(`/ravn?ravn=${RAVN_ID}&tab=activity`, services);
    expect(await screen.findByText('The runtime reported no log lines.')).toBeInTheDocument();
  });

  it('explains when the profile exposes no logs', async () => {
    mountRavn(
      `/ravn?ravn=${RAVN_ID}&tab=activity`,
      ravnServices({ ravens: [makeRavn({ capabilities: ['chat'] })] }),
    );
    expect(await screen.findByText(/does not expose runtime logs/)).toBeInTheDocument();
  });

  it('shows session activity for ravens that are not managed', async () => {
    const services = ravnServices({
      ravens: [makeRavn({ managed: false, kind: 'persona', observedState: undefined })],
    });
    mountRavn(`/ravn?ravn=${RAVN_ID}&tab=activity`, services);
    expect(await screen.findByText('No recorded activity for this ravn.')).toBeInTheDocument();
  });
});

describe('Setup tab', () => {
  it('shows persona, runtime, capabilities, health and identifiers', async () => {
    mountRavn(
      `/ravn?ravn=${RAVN_ID}&tab=setup`,
      ravnServices({
        ravens: [
          makeRavn({
            endpoints: [{ kind: 'metrics', protocol: 'http', url: 'http://m:9200/metrics' }],
            mcpServers: ['kubernetes'],
            eventSubscriptions: ['signal.received'],
            mounts: [{ name: 'shared', role: 'ro' }],
            flockId: '66666666-6666-4666-8666-666666666666',
            flockRole: 'coordinator',
            flockPeerId: 'peer-1',
            sessionId: 'other-session',
            backendRef: { kind: 'DockerContainer', name: 'resident-1' },
          }),
        ],
      }),
    );
    const setup = await screen.findByTestId('ravn-setup-tab');
    expect(await within(setup).findByText(/25 iterations max/)).toBeInTheDocument();
    const caps = within(setup).getByTestId('ravn-capabilities');
    expect(within(caps).getByText('Chat').closest('.rw-cap')).toHaveAttribute('data-on', 'true');
    expect(within(caps).getByText('Approvals').closest('.rw-cap')).toHaveAttribute(
      'data-on',
      'false',
    );
    expect(within(setup).getByTestId('ravn-conditions')).toHaveTextContent('BackendReady');
    expect(setup).toHaveTextContent('http://m:9200/metrics');
    expect(setup).toHaveTextContent('kubernetes');
    expect(setup).toHaveTextContent('shared (ro)');
    expect(setup).toHaveTextContent('coordinator');
    expect(setup).toHaveTextContent('DockerContainer · resident-1');
  });

  it('says when a ravn runs without a persona', async () => {
    mountRavn(
      `/ravn?ravn=${RAVN_ID}&tab=setup`,
      ravnServices({ ravens: [makeRavn({ personaName: '', engine: 'openclaw' })] }),
    );
    expect(await screen.findByText('Engine default')).toBeInTheDocument();
    expect(screen.getByText(/openclaw runs with its own built-in character/)).toBeInTheDocument();
  });

  it('opens the persona from setup', async () => {
    const router = mountRavn(`/ravn?ravn=${RAVN_ID}&tab=setup`, ravnServices());
    fireEvent.click(await screen.findByRole('button', { name: 'Open persona →' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/ravn/personas'));
  });
});

describe('Usage tab', () => {
  it('shows usage, conversations and the budget', async () => {
    mountRavn(
      `/ravn?ravn=${RAVN_ID}&tab=usage`,
      ravnServices({ sessions: [makeSession({ title: 'Kernel flake', messageCount: 18 })] }),
    );
    const usage = await screen.findByTestId('ravn-usage-tab');
    expect(usage).toHaveTextContent('34.9k');
    expect(usage).toHaveTextContent('$0.07');
    expect(await within(usage).findByText('Kernel flake')).toBeInTheDocument();
    expect(await within(usage).findByTestId('ravn-budget')).toHaveTextContent('$1.00 of $4.00');
  });

  it('says why there is no budget', async () => {
    const services = ravnServices({ ravens: [makeRavn({ costUsd: undefined })] });
    services['ravn.budget'].getBudget = vi.fn().mockRejectedValue(
      Object.assign(new Error('API request failed: 503'), {
        detail: 'Ravn budget persistence is unavailable',
      }),
    );
    mountRavn(`/ravn?ravn=${RAVN_ID}&tab=usage`, services);
    expect(await screen.findByTestId('ravn-budget-unavailable')).toHaveTextContent(
      'Ravn budget persistence is unavailable',
    );
    expect(screen.getByText('not reported')).toBeInTheDocument();
  });
});
