import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { mountRavn, ravnServices } from '../../testing/mountRavn';
import { makeSession } from '../../testing/fixtures';

vi.mock('./LiveChat', () => ({
  LiveChat: ({ chatEndpoint }: { chatEndpoint: string }) => (
    <div data-testid="live-chat">{chatEndpoint}</div>
  ),
}));

vi.mock('@niuulabs/plugin-volundr', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@niuulabs/plugin-volundr')>()),
  LiveLogsTab: ({ sessionId }: { sessionId: string }) => (
    <div data-testid="forge-logs">{sessionId}</div>
  ),
}));

beforeEach(() => localStorage.clear());

const FLOCK = '77777777-7777-4777-8777-777777777777';

function flockSession(overrides = {}) {
  return makeSession({
    id: FLOCK,
    ravnId: FLOCK,
    title: 'huginn-scout',
    model: '',
    instanceId: undefined,
    chatEndpoint: 'ws://localhost/s/flock/session',
    ...overrides,
  });
}

describe('Forge-backed ravens', () => {
  it('lists a flock session as a ravn and talks to it', async () => {
    mountRavn(`/ravn?ravn=${FLOCK}`, ravnServices({ ravens: [], sessions: [flockSession()] }));
    const row = await screen.findByTestId(`ravn-row-${FLOCK}`);
    expect(row).toHaveTextContent('huginn-scout');
    expect(row).toHaveTextContent('session');
    expect(await screen.findByTestId('live-chat')).toHaveTextContent(/\/s\/flock\/session$/);
    expect(screen.getByTestId('ravn-open-forge-session')).toHaveTextContent('Forge session');
    expect(screen.queryByTestId('ravn-delete')).not.toBeInTheDocument();
    expect(screen.queryByTestId('ravn-restart')).not.toBeInTheDocument();
  });

  it('opens the session in Forge from the header', async () => {
    const router = mountRavn(
      `/ravn?ravn=${FLOCK}`,
      ravnServices({ ravens: [], sessions: [flockSession()] }),
    );
    fireEvent.click(await screen.findByTestId('ravn-open-forge-session'));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/volundr/sessions/${FLOCK}`));
  });

  it('stops it after confirmation and returns to the list', async () => {
    const services = ravnServices({ ravens: [], sessions: [flockSession()] });
    const router = mountRavn(`/ravn?ravn=${FLOCK}`, services);
    fireEvent.click(await screen.findByTestId('ravn-stop'));
    const dialog = await screen.findByRole('dialog', { name: 'Stop huginn-scout' });
    fireEvent.click(within(dialog).getByTestId('ravn-stop-confirm'));
    await waitFor(() =>
      expect(services['ravn.sessions'].stopSession).toHaveBeenCalledWith(FLOCK, undefined),
    );
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });

  it('shows why a stop failed', async () => {
    const services = ravnServices({ ravens: [], sessions: [flockSession()] });
    services['ravn.sessions'].stopSession = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'Forge rejected session stop' }));
    mountRavn(`/ravn?ravn=${FLOCK}`, services);
    fireEvent.click(await screen.findByTestId('ravn-stop'));
    fireEvent.click(await screen.findByTestId('ravn-stop-confirm'));
    expect(await screen.findByText('Forge rejected session stop')).toBeInTheDocument();
  });

  it('shows the Forge session logs as its activity', async () => {
    mountRavn(
      `/ravn?ravn=${FLOCK}&tab=activity`,
      ravnServices({ ravens: [], sessions: [flockSession()], withForge: true }),
    );
    expect(await screen.findByTestId('forge-logs')).toHaveTextContent(FLOCK);
  });

  it('says where its logs live when no Forge is wired', async () => {
    mountRavn(
      `/ravn?ravn=${FLOCK}&tab=activity`,
      ravnServices({ ravens: [], sessions: [flockSession()] }),
    );
    expect(await screen.findByText('No session logs here')).toBeInTheDocument();
  });

  it('explains what it runs as in Setup', async () => {
    const router = mountRavn(
      `/ravn?ravn=${FLOCK}&tab=setup`,
      ravnServices({ ravens: [], sessions: [flockSession()] }),
    );
    expect(await screen.findByTestId('ravn-setup-session')).toHaveTextContent(
      'A Forge session on this Forge',
    );
    expect(screen.getByText('set by the Forge’s ravn configuration')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open in Forge →' }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/volundr/sessions/${FLOCK}`));
  });

  it('follows a starting session until it runs', async () => {
    const services = ravnServices({ ravens: [] });
    services['ravn.sessions'].listSessions = vi
      .fn()
      .mockResolvedValueOnce([flockSession({ status: 'idle' })])
      .mockResolvedValue([flockSession()]);
    mountRavn(`/ravn?ravn=${FLOCK}`, services);
    expect(await screen.findByTestId('ravn-health-banner')).toHaveTextContent(
      'Starting on this Forge',
    );
    expect(await screen.findByTestId('live-chat', {}, { timeout: 4_000 })).toBeInTheDocument();
  });

  it('says when the sessions behind Forge-backed ravens cannot be listed', async () => {
    const services = ravnServices();
    services['ravn.sessions'].listSessions = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'Forge offline' }));
    mountRavn('/ravn', services);
    expect(await screen.findByTestId('ravn-sessions-error')).toHaveTextContent('Forge offline');
  });
});

describe('Deploying a ravn as a Forge session', () => {
  it('leads with the container-free runtime and starts a flock session', async () => {
    const services = ravnServices({ withForge: true });
    const router = mountRavn('/ravn', services);
    fireEvent.click(await screen.findByTestId('ravn-deploy-open'));
    const dialog = await screen.findByRole('dialog', { name: 'Deploy a ravn' });
    expect(within(dialog).getByTestId('ravn-deploy-runtime-session')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(dialog).getByTestId('ravn-deploy-session-model')).toHaveTextContent(
      'Set by this Forge’s ravn configuration',
    );
    const summary = within(dialog).getByTestId('ravn-deploy-summary');
    expect(summary).toHaveTextContent('Name it and pick a persona.');

    fireEvent.change(within(dialog).getByTestId('ravn-deploy-name'), {
      target: { value: '!!!' },
    });
    expect(summary).toHaveTextContent('Give it a name with at least one letter or digit.');
    fireEvent.change(within(dialog).getByTestId('ravn-deploy-name'), {
      target: { value: 'Huginn Scout' },
    });
    expect(summary).toHaveTextContent('Pick the persona it runs.');
    expect(within(dialog).getByTestId('ravn-deploy-submit')).toBeDisabled();
    expect(within(dialog).queryByText('Engine default')).not.toBeInTheDocument();

    fireEvent.click(await within(dialog).findByText('research-analyst'));
    expect(summary).toHaveTextContent(
      'Starts huginn-scout (Forge names sessions in lowercase) as a Ravn session on this Forge with persona research-analyst.',
    );
    fireEvent.click(within(dialog).getByTestId('ravn-deploy-submit'));
    await waitFor(() =>
      expect(services.volundr!.startSession).toHaveBeenCalledWith({
        name: 'huginn-scout',
        model: '',
        source: { type: 'git', repo: '', branch: 'main' },
        workloadType: 'ravn_flock',
        workloadConfig: { personas: ['research-analyst'] },
      }),
    );
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ ravn: 'forge-session-1', tab: 'chat' }),
    );
  });

  it('shows why the session could not start', async () => {
    const services = ravnServices({ withForge: true });
    services.volundr!.startSession = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'max concurrent sessions' }));
    mountRavn('/ravn', services);
    fireEvent.click(await screen.findByTestId('ravn-deploy-open'));
    const dialog = await screen.findByRole('dialog', { name: 'Deploy a ravn' });
    fireEvent.change(await within(dialog).findByTestId('ravn-deploy-name'), {
      target: { value: 'scout' },
    });
    expect(within(dialog).getByTestId('ravn-deploy-summary')).toHaveTextContent(
      'Pick the persona it runs.',
    );
    fireEvent.click(await within(dialog).findByText('reviewer'));
    fireEvent.click(screen.getByTestId('ravn-deploy-submit'));
    expect(await screen.findByText('max concurrent sessions')).toBeInTheDocument();
  });

  it('asks for a name once the persona is picked', async () => {
    mountRavn('/ravn', ravnServices({ withForge: true }));
    fireEvent.click(await screen.findByTestId('ravn-deploy-open'));
    const dialog = await screen.findByRole('dialog', { name: 'Deploy a ravn' });
    fireEvent.click(await within(dialog).findByText('research-framer'));
    expect(within(dialog).getByTestId('ravn-deploy-summary')).toHaveTextContent('Name it.');
  });

  it('switches to a resident profile and back', async () => {
    mountRavn('/ravn', ravnServices({ withForge: true }));
    fireEvent.click(await screen.findByTestId('ravn-deploy-open'));
    fireEvent.click(await screen.findByTestId('ravn-deploy-profile-nemoclaw-local'));
    expect(screen.queryByTestId('ravn-deploy-session-model')).not.toBeInTheDocument();
    expect(await screen.findByText('Engine default')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('ravn-deploy-runtime-session'));
    expect(screen.getByTestId('ravn-deploy-session-model')).toBeInTheDocument();
  });
});
