import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createSeedRealms } from '../adapters/mock';
import {
  createCallLog,
  failingTing,
  fakeRealmService,
  fakeVolundr,
  withoutTing,
} from '../testing/fakes';
import { renderRealms } from '../testing/renderRealms';

const PROJECT = '/realms/projects/project-yggdrasil';

async function boundRealms() {
  const realms = fakeRealmService(createCallLog(), createSeedRealms());
  await realms.createTrustGrant('valhalla', {
    action_class: 'observe',
    target: 'niuulabs/volundr',
    level: 2,
    limits: { tracker_board: 'board-1', bug_board: 'board-2', mount_target: 'ymir' },
  });
  return { 'realms.governance': realms };
}

function settled() {
  return waitFor(() => expect(screen.queryByText(/^Loading/)).not.toBeInTheDocument());
}

describe('a host without Ting', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('lists its projects without a word on what waits', async () => {
    const { services, asked } = withoutTing();
    renderRealms('/realms', { ...(await boundRealms()), ...services });

    const card = await screen.findByTestId('realm-card-valhalla');
    await settled();
    expect(within(card).queryByText('in sessions')).not.toBeInTheDocument();
    expect(within(card).queryByText('needs you')).not.toBeInTheDocument();
    expect(screen.queryByTestId('needs-you-panel')).not.toBeInTheDocument();
    expect(screen.queryByTestId('needs-you-error')).not.toBeInTheDocument();
    expect(screen.queryByText('Nothing waiting on you.')).not.toBeInTheDocument();
    expect(screen.queryByText('awaiting you')).not.toBeInTheDocument();
    expect(screen.getByText('workspaces')).toBeInTheDocument();
    expect(asked).toEqual([]);
  });

  it('says on the needs-you page that it holds no tickets', async () => {
    const { services, asked } = withoutTing();
    renderRealms('/realms/needs-you', services);

    expect(await screen.findByText('No tickets on this host')).toBeInTheDocument();
    expect(screen.queryByText('Nothing waiting on you')).not.toBeInTheDocument();
    expect(asked).toEqual([]);
  });

  it("shows a project's sessions and artifacts, and no needs-you section", async () => {
    const { services, asked } = withoutTing();
    renderRealms(PROJECT, services);

    const page = await screen.findByTestId('project-page');
    expect(
      await within(page).findByText('No session has presented a document yet.'),
    ).toBeInTheDocument();
    expect(within(page).getByText(/^Sessions ·/)).toBeInTheDocument();
    expect(within(page).queryByText(/Needs you/)).not.toBeInTheDocument();
    expect(within(page).queryByText('in sessions')).not.toBeInTheDocument();
    expect(within(page).queryByRole('alert')).not.toBeInTheDocument();
    expect(asked).toEqual([]);
  });

  it('keeps Home to what its own sessions say', async () => {
    const { services, asked } = withoutTing();
    renderRealms('/home', services);

    await screen.findByTestId('home-needs-you');
    expect(screen.getByText('No session is waiting on you.')).toBeInTheDocument();
    expect(screen.queryByText('Nothing is waiting on you.')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'see all' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('home-choice-workflow')).not.toBeInTheDocument();
    expect(screen.getByTestId('home-choice-session')).toBeInTheDocument();
    // No board count is guessed at.
    expect(screen.queryByText(/boards?$/)).not.toBeInTheDocument();
    expect(asked).toEqual([]);
  });

  it('leaves out the parts of a workspace that live in Ting', async () => {
    const { services, asked } = withoutTing();
    renderRealms('/realms/valhalla?tab=board', { ...(await boundRealms()), ...services });

    await screen.findByTestId('realm-page');
    const tabs = screen.getByRole('group', { name: 'Workspace section' });
    expect(within(tabs).queryByRole('button', { name: /Board/ })).not.toBeInTheDocument();
    expect(within(tabs).queryByRole('button', { name: /Sessions/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId('realm-run-workflow')).not.toBeInTheDocument();
    expect(screen.queryByTestId('queue-intake')).not.toBeInTheDocument();
    expect(screen.getByTestId('trust-summary')).toBeInTheDocument();

    await userEvent.click(within(tabs).getByText('Connections'));
    expect(await screen.findByTestId('realm-connections')).toBeInTheDocument();
    expect(screen.queryByTestId('connection-tracker')).not.toBeInTheDocument();
    expect(asked).toEqual([]);
  });

  it('sets a workspace up without a board', async () => {
    const { services, asked } = withoutTing();
    renderRealms('/realms/new?sentence=Work+the+LXA+board+of+niuulabs/lexi-api', services);

    await screen.findByTestId('new-realm');
    expect(await screen.findByText(/its memory and its trust\./)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Review step by step' }));
    expect(await screen.findByTestId('wizard-step-connect')).toBeInTheDocument();
    expect(screen.queryByText('Tracker board')).not.toBeInTheDocument();
    expect(asked).toEqual([]);
  });

  it("offers no ticket import in a project's memory settings", async () => {
    const { services, asked } = withoutTing();
    renderRealms(`${PROJECT}/memory`, {
      volundr: { ...fakeVolundr(createCallLog()), getTeamIntegrations: async () => [] },
      ...services,
    });

    await screen.findByTestId('project-memory-page');
    expect(
      await screen.findByRole('textbox', { name: 'Folders agents may write to' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('Bring in tickets')).not.toBeInTheDocument();
    expect(asked).toEqual([]);
  });
});

describe('a configured Ting that fails', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it.each(['/realms', '/realms/needs-you', PROJECT, '/home'])(
    'shows its error on %s, even a 404',
    async (path) => {
      renderRealms(path, failingTing());
      expect(await screen.findByTestId('needs-you-error')).toHaveTextContent(
        'API request failed: 404',
      );
    },
  );

  it("shows its error in a project's artifacts", async () => {
    renderRealms(PROJECT, {
      ...failingTing(),
      'ting.runLog': {
        hosts: vi.fn().mockResolvedValue([null]),
        publications: vi.fn().mockResolvedValue([]),
        projectSessions: vi.fn().mockResolvedValue([]),
        session: vi.fn(),
        presentedText: vi.fn(),
        sendMessage: vi.fn(),
      },
    });
    const page = await screen.findByTestId('project-page');
    expect(await within(page).findByRole('alert')).toHaveTextContent('API request failed: 404');
  });
});
