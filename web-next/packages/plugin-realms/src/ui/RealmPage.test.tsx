import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createSeedRealms } from '../adapters/mock';
import {
  createCallLog,
  fakeRealmService,
  fakeTracker,
  workspaceWithWaitingTicket,
} from '../testing/fakes';
import { renderRealms } from '../testing/renderRealms';

async function realmWithBinding(log = createCallLog()) {
  const realms = fakeRealmService(log, createSeedRealms());
  await realms.createTrustGrant('valhalla', {
    action_class: 'observe',
    target: 'niuulabs/volundr',
    level: 2,
    limits: {
      template: 'product-resident',
      tracker_board: 'board-1',
      branch: 'dev',
      mount_target: 'ymir',
    },
  });
  await realms.createTrustGrant('valhalla', {
    action_class: 'deploy',
    target: '*',
    level: 1,
    limits: {},
  });
  return realms;
}

describe('RealmPage', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('shows the realm with its counts and trust', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    renderRealms('/realms/valhalla', { 'realms.governance': await realmWithBinding(log) }, log);
    await screen.findByTestId('realm-page');
    expect(screen.getByText('Valhalla')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('queue-intake')).toHaveTextContent('Intake1'));
    expect(screen.getByRole('group', { name: 'Workspace section' })).toBeInTheDocument();
    expect(screen.getByText('observe · L2')).toBeInTheDocument();
    expect(screen.getByText('deploy · L1')).toBeInTheDocument();
    expect(screen.getByText(/board board-1/)).toBeInTheDocument();
    // The counts are the overview; the tickets themselves live one click away.
    expect(screen.queryByText(/LXA-1/)).not.toBeInTheDocument();
    await user.click(screen.getByTestId('queue-intake'));
    expect(await screen.findByText(/LXA-1/)).toBeInTheDocument();
  });

  it('shows a ticket at a gate at the top of the overview', async () => {
    const log = createCallLog();
    renderRealms('/realms/valhalla', await workspaceWithWaitingTicket(log, 'valhalla'), log);
    expect(await screen.findByTestId('needs-you-ticket-run-1')).toBeInTheDocument();
    expect(screen.getByText('Needs you · 1')).toBeInTheDocument();
    expect(screen.getByText('1 need you')).toBeInTheDocument();
  });

  it('links back to the project the workspace serves', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const { router } = renderRealms(
      '/realms/valhalla',
      { 'realms.governance': await realmWithBinding(log) },
      log,
    );
    const link = await screen.findByTestId('realm-project-link');
    await waitFor(() => expect(link).toHaveTextContent('Yggdrasil'));
    await user.click(link);
    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/realms/projects/project-yggdrasil'),
    );
  });

  it('has no project link on a workspace outside any project', async () => {
    renderRealms('/realms/host-jozef');
    await screen.findByTestId('realm-page');
    expect(screen.queryByTestId('realm-project-link')).not.toBeInTheDocument();
  });

  it('names the tracker connection when it reads the tickets', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const seen: unknown[][] = [];
    const tracker = fakeTracker(log);
    const listIssues = tracker.listIssues.bind(tracker);
    tracker.listIssues = async (...args) => {
      seen.push(args);
      return listIssues(...args);
    };
    renderRealms(
      '/realms/valhalla',
      {
        'realms.governance': await realmWithBinding(log),
        'ting.tracker': tracker,
        ting: {
          getSagas: async () => [
            {
              id: 'saga-1',
              name: 'Board one',
              trackerId: 'board-1',
              trackerConnectionId: 'conn-1',
            },
          ],
        },
        'ting.work': {
          get: async () => ({
            board: null,
            tasks: [
              {
                source: { issueId: 'i-1', identifier: 'LXA-1', url: 'https://linear.app/x/LXA-1' },
                title: 'First ticket',
              },
            ],
            coverage: [],
          }),
        },
      },
      log,
    );
    await screen.findByTestId('realm-page');
    await waitFor(() => expect(seen).toContainEqual(['board-1', undefined, 'conn-1']));
    expect(await screen.findByText(/board Board one/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^Board/ }));
    expect(await screen.findByText(/LXA-1/)).toBeInTheDocument();
  });

  it('says so when the tickets could not be read, rather than showing an empty board', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const tracker = fakeTracker(log);
    tracker.listIssues = async () => {
      throw new Error('This project exists in two connections');
    };
    renderRealms(
      '/realms/valhalla',
      { 'realms.governance': await realmWithBinding(log), 'ting.tracker': tracker },
      log,
    );
    await screen.findByTestId('realm-page');
    await user.click(screen.getByRole('button', { name: /^Board/ }));
    expect(await screen.findByText('Could not load the tickets')).toBeInTheDocument();
    expect(await screen.findByText(/two connections/)).toBeInTheDocument();
  });

  it('explains when the realm does not exist', async () => {
    renderRealms('/realms/nowhere');
    await screen.findByText('No such workspace');
  });

  it('opens the launch wizard and the workflow modal from the header', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    renderRealms('/realms/valhalla', { 'realms.governance': await realmWithBinding(log) }, log);
    await screen.findByTestId('realm-page');
    await waitFor(() => expect(screen.getByTestId('realm-run-workflow')).toBeEnabled());
    await user.click(screen.getByTestId('realm-run-workflow'));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await user.click(screen.getByTestId('realm-launch-session'));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('sends the old settings route to the Settings tab', async () => {
    const log = createCallLog();
    const { router } = renderRealms(
      '/realms/valhalla/settings',
      { 'realms.governance': await realmWithBinding(log) },
      log,
    );
    await screen.findByTestId('realm-settings');
    await waitFor(() => expect(router.state.location.pathname).toBe('/realms/valhalla'));
    expect(router.state.location.search).toMatchObject({ tab: 'settings' });
  });

  it('keeps trust and deletion in the Settings tab', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    renderRealms('/realms/valhalla', { 'realms.governance': await realmWithBinding(log) }, log);
    await screen.findByTestId('realm-page');
    expect(screen.queryByRole('button', { name: 'Resident' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Settings' }));
    await screen.findByTestId('realm-settings');
    expect(await screen.findByText('What it may do on its own')).toBeInTheDocument();
    expect(screen.getByTestId('realm-delete')).toBeInTheDocument();
  });

  it('changes a trust rung by adding a grant', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    renderRealms(
      '/realms/valhalla?tab=settings',
      { 'realms.governance': await realmWithBinding(log) },
      log,
    );
    await screen.findByTestId('trust-panel');
    await user.click(
      within(screen.getByRole('group', { name: 'deploy trust' })).getByRole('button', {
        name: 'on its own',
      }),
    );
    await waitFor(() => expect(log.calls).toContain('createTrustGrant:valhalla:deploy:2'));
  });

  it('says a workspace made without a memory target has no memory of its own', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const realms = fakeRealmService(log, createSeedRealms());
    await realms.createTrustGrant('valhalla', {
      action_class: 'observe',
      target: '*',
      level: 2,
      limits: { tracker_board: 'board-1', mount_target: '' },
    });
    renderRealms('/realms/valhalla', { 'realms.governance': realms }, log);
    await screen.findByTestId('realm-page');
    await user.click(screen.getByRole('button', { name: 'Memory' }));
    expect(await screen.findByText('No workspace memory')).toBeInTheDocument();
    expect(screen.queryByText('Workspace memory is still starting')).not.toBeInTheDocument();
  });
});
