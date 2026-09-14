import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createSeedRealms } from '@niuulabs/plugin-valkyrie';
import { createCallLog, fakeRealmService } from '../testing/fakes';
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

  it('shows the realm with its resident, queues, trust and budget', async () => {
    const log = createCallLog();
    renderRealms('/realms/valhalla', { 'valkyrie.realms': await realmWithBinding(log) }, log);
    await screen.findByTestId('realm-page');
    expect(screen.getByText('Valhalla')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/LXA-1/)).toBeInTheDocument());
    expect(screen.getByTestId('queue-intake')).toHaveTextContent('Intake1');
    expect(screen.getByRole('group', { name: 'Realm section' })).toBeInTheDocument();
    expect(screen.getByText('observe · L2')).toBeInTheDocument();
    expect(screen.getByText('deploy · L1')).toBeInTheDocument();
    expect(screen.getByText(/board board-1/)).toBeInTheDocument();
  });

  it('explains when the realm does not exist', async () => {
    renderRealms('/realms/nowhere');
    await screen.findByText('No such realm');
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

  it('routes to settings and back', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const { router } = renderRealms(
      '/realms/valhalla',
      { 'valkyrie.realms': await realmWithBinding(log) },
      log,
    );
    await screen.findByTestId('realm-page');
    await user.click(screen.getByRole('link', { name: 'Settings' }));
    await screen.findByTestId('realm-settings');
    expect(router.state.location.pathname).toBe('/realms/valhalla/settings');
    await user.click(screen.getByRole('button', { name: 'Trust' }));
    expect(await screen.findByText('What it may do on its own')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Standing jobs' }));
    expect(await screen.findByText(/Triggers that wake the resident/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Memory' }));
    expect(await screen.findByText(/created with the realm/)).toBeInTheDocument();
  });
});
