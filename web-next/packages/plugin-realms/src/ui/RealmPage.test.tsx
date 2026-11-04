import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Ravn } from '@niuulabs/plugin-ravn';
import { createMockValkyrieService, createSeedRealms } from '@niuulabs/plugin-valkyrie';
import {
  createCallLog,
  fakePersonas,
  fakeRealmService,
  fakeResidents,
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

  it('shows the realm with its resident, counts, trust and budget', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    renderRealms('/realms/valhalla', { 'valkyrie.realms': await realmWithBinding(log) }, log);
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

  it('talks to the resident the workspace was adopted around', async () => {
    const log = createCallLog();
    const residents = fakeResidents(log);
    // The mock dashboard's Valhalla resident, registered under its own id with no realm link.
    const sigrun = {
      id: 'valkyrie-valhalla-sigrun',
      residentName: 'Sigrun',
      personaName: 'k8s-valkyrie',
      status: 'active',
      kind: 'resident',
      managed: false,
    } as unknown as Ravn;
    const services = {
      'valkyrie.realms': await realmWithBinding(log),
      'ravn.residents': residents,
      'ravn.ravens': { ...residents, listRavens: async () => [sigrun] },
    };
    renderRealms('/realms/valhalla', services, log);
    await screen.findByTestId('realm-page');
    expect(
      await screen.findByText('No conversation yet. Ask it anything about the workspace.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('No resident yet.')).not.toBeInTheDocument();
  });

  it('says why the budget cannot be read instead of reporting nothing spent', async () => {
    const log = createCallLog();
    const residents = fakeResidents(log);
    const sigrun = {
      id: 'valkyrie-valhalla-sigrun',
      residentName: 'Sigrun',
      status: 'active',
    } as unknown as Ravn;
    const unavailable = Object.assign(new Error('API request failed: 503'), {
      detail: 'Ravn budget persistence is unavailable',
    });
    renderRealms(
      '/realms/valhalla',
      {
        'valkyrie.realms': await realmWithBinding(log),
        'ravn.ravens': { ...residents, listRavens: async () => [sigrun] },
        'ravn.budget': {
          getBudget: async () => {
            throw unavailable;
          },
          getFleetBudget: async () => {
            throw unavailable;
          },
        },
      },
      log,
    );
    expect(
      await screen.findByText(/Budget unavailable: Ravn budget persistence is unavailable/),
    ).toBeInTheDocument();
    expect(screen.queryByText('Nothing spent yet.')).not.toBeInTheDocument();
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
      { 'valkyrie.realms': await realmWithBinding(log) },
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
        'valkyrie.realms': await realmWithBinding(log),
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
      { 'valkyrie.realms': await realmWithBinding(log), 'ting.tracker': tracker },
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
    renderRealms('/realms/valhalla', { 'valkyrie.realms': await realmWithBinding(log) }, log);
    await screen.findByTestId('realm-page');
    await waitFor(() => expect(screen.getByTestId('realm-run-workflow')).toBeEnabled());
    await user.click(screen.getByTestId('realm-run-workflow'));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await user.click(screen.getByTestId('realm-launch-session'));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('sends the old settings route to the Resident tab', async () => {
    const log = createCallLog();
    const { router } = renderRealms(
      '/realms/valhalla/settings',
      { 'valkyrie.realms': await realmWithBinding(log) },
      log,
    );
    await screen.findByTestId('realm-settings');
    await waitFor(() => expect(router.state.location.pathname).toBe('/realms/valhalla'));
    expect(router.state.location.search).toMatchObject({ tab: 'resident' });
  });

  it('keeps the resident in its own tab', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    renderRealms('/realms/valhalla', { 'valkyrie.realms': await realmWithBinding(log) }, log);
    await screen.findByTestId('realm-page');
    await user.click(screen.getByRole('button', { name: 'Resident' }));
    await screen.findByTestId('realm-settings');
    await user.click(screen.getByRole('button', { name: 'Trust' }));
    expect(await screen.findByText('What it may do on its own')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Standing jobs' }));
    expect(await screen.findByText(/Triggers that wake the resident/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Memory' }));
    expect(await screen.findByText(/realms\/valhalla\/charter.md/)).toBeInTheDocument();
  });

  it("shows an adopted resident's own charter, lifecycle and memory", async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const realms = fakeRealmService(log, createSeedRealms());
    // Adopting names a board but no memory target: nothing was deployed for the workspace.
    await realms.createTrustGrant('valhalla', {
      action_class: 'observe',
      target: '*',
      level: 2,
      limits: { tracker_board: 'board-1', mount_target: '' },
    });
    const valkyrie = createMockValkyrieService();
    const dashboard = valkyrie.getDashboard.bind(valkyrie);
    renderRealms(
      '/realms/valhalla',
      {
        'valkyrie.realms': realms,
        valkyrie: {
          ...valkyrie,
          getDashboard: async () => {
            const data = await dashboard();
            return {
              ...data,
              valkyries: data.valkyries.map((entry) => ({
                ...entry,
                charter: 'Keep **valhalla** healthy.',
              })),
            };
          },
        },
        // The resident's persona lives in its own deployment, not the registry.
        'ravn.personas': {
          ...fakePersonas(log),
          getPersona: async () => Promise.reject(Object.assign(new Error('nope'), { status: 404 })),
        },
      },
      log,
    );
    await screen.findByTestId('realm-page');
    await user.click(screen.getByRole('button', { name: 'Resident' }));
    const charter = await screen.findByTestId('resident-charter');
    expect(charter.querySelector('strong')).toHaveTextContent('valhalla');
    expect(screen.getByText(/Sigrun runs persona cluster-guardian/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Lifecycle' }));
    expect(await screen.findByText(/Sigrun was running before the workspace/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Memory' }));
    expect(
      await screen.findByText(/Sigrun has not reported where it keeps its memory/),
    ).toBeInTheDocument();
    expect(screen.queryByText('Workspace memory is still starting')).not.toBeInTheDocument();
  });
});
