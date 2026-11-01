import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createCallLog, fakeRealmService, fakeVolundr } from '../testing/fakes';
import { createSeedRealms } from '@niuulabs/plugin-valkyrie';
import { renderRealms } from '../testing/renderRealms';

async function open(log = createCallLog()) {
  const user = userEvent.setup();
  const realms = fakeRealmService(log, createSeedRealms());
  const view = renderRealms(
    '/realms/host-jozef?tab=connections',
    { 'valkyrie.realms': realms, volundr: fakeVolundr(log) },
    log,
  );
  await screen.findByTestId('realm-connections');
  return { user, realms, log, ...view };
}

describe('ConnectionsPanel', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('says a workspace works without a tracker', async () => {
    await open();
    expect(screen.getByText(/A workspace works without one/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Connect a tracker' })).toBeInTheDocument();
  });

  it('moves the workspace into an existing project', async () => {
    const { user, realms, log } = await open();
    const card = screen.getByTestId('connection-project');
    expect(within(card).getByRole('button', { name: 'Move workspace' })).toBeDisabled();
    await within(card).findByRole('option', { name: 'Yggdrasil' });
    await user.selectOptions(within(card).getByRole('combobox'), 'project-yggdrasil');
    await user.click(within(card).getByRole('button', { name: 'Move workspace' }));
    await waitFor(() =>
      expect(log.calls).toContain('setRealmProject:host-jozef->project-yggdrasil'),
    );
    expect((await realms.getRealm('host-jozef')).project_id).toBe('project-yggdrasil');
  });

  it('creates a project to move the workspace into', async () => {
    const { user, log } = await open();
    const card = screen.getByTestId('connection-project');
    await user.selectOptions(within(card).getByRole('combobox'), 'new');
    await user.type(screen.getByRole('textbox', { name: 'New project name' }), 'Home lab');
    await user.click(within(card).getByRole('button', { name: 'Move workspace' }));
    await waitFor(() => expect(log.calls).toContain('createProject:Home lab'));
    expect(log.calls).toContain('setRealmProject:host-jozef->project-new');
  });

  it('opens the tracker dialog from the board tab when none is connected', async () => {
    const user = userEvent.setup();
    renderRealms('/realms/host-jozef?tab=board');
    await user.click(await screen.findByRole('button', { name: 'Connect a tracker' }));
    expect(await screen.findByTestId('realm-connections')).toBeInTheDocument();
  });

  it('shows the workflows the board runs, which the project list leaves out', async () => {
    const log = createCallLog();
    const realms = fakeRealmService(log, createSeedRealms());
    await realms.createTrustGrant('valhalla', {
      action_class: 'observe',
      target: '*',
      level: 2,
      limits: { tracker_board: 'board-1' },
    });
    const listed = { id: 'saga-1', name: 'Board one', trackerId: 'board-1', trackerType: 'linear' };
    renderRealms(
      '/realms/valhalla?tab=connections',
      {
        'valkyrie.realms': realms,
        ting: {
          getSagas: async () => [listed],
          getSaga: async () => ({
            ...listed,
            boardWorkflows: [{ id: 'wf-1', name: 'Ticket Work Flow' }],
          }),
        },
        'ting.work': { get: async () => ({ board: null, tasks: [], coverage: [] }) },
      },
      log,
    );
    const card = await screen.findByRole('region', { name: 'Board workflows' });
    expect(within(card).getByText(/Ticket Work Flow/)).toBeInTheDocument();
    expect(
      within(screen.getByTestId('connection-tracker')).getByText(/Board one/),
    ).toBeInTheDocument();
  });

  it('binds an imported tracker to the workspace', async () => {
    const { user, log } = await open();
    await user.click(screen.getByRole('button', { name: 'Connect a tracker' }));
    const dialog = await screen.findByRole('dialog');
    const project = await within(dialog).findByRole('button', { name: /Lexi|board|Board/ });
    await user.click(project);
    await user.click(await within(dialog).findByRole('button', { name: 'Import project' }));
    await waitFor(() =>
      expect(log.calls.some((call) => call.startsWith('importProject:'))).toBe(true),
    );
    await waitFor(() =>
      expect(log.calls.some((call) => call.includes('createTrustGrant'))).toBe(true),
    );
  });
});
