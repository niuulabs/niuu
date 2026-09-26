import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { UI_MODE_STORAGE_KEY } from '@niuulabs/shell';
import { createSeedRealms } from '@niuulabs/plugin-valkyrie';
import {
  createCallLog,
  fakeMimir,
  fakePersonas,
  fakeRealmService,
  fakeResidents,
  fakeVolundr,
} from '../testing/fakes';
import { renderRealms } from '../testing/renderRealms';

async function populated(log = createCallLog()) {
  const realms = fakeRealmService(log, createSeedRealms());
  await realms.createTrustGrant('valhalla', {
    action_class: 'observe',
    target: 'niuulabs/volundr',
    level: 2,
    limits: {
      template: 'product-resident',
      tracker_board: 'board-1',
      bug_board: 'board-1',
      branch: 'dev',
      mount_target: 'ymir',
    },
  });
  const residents = fakeResidents(log);
  await residents.deploy({
    name: 'valhalla',
    profileId: 'profile-1',
    instanceId: 'inst-1',
    personaName: 'realm-valhalla',
  });
  residents.listSessions = async () =>
    [
      {
        id: 'sess-1',
        ravnId: 'ravn-1',
        personaName: 'realm-valhalla',
        status: 'running',
        model: 'm',
        createdAt: '',
      },
    ] as never;
  const mimir = fakeMimir(log);
  await mimir.mounts.deployInstance!({ name: 'realm-valhalla', backend: 'mimir', target: 'ymir' });
  const volundr = fakeVolundr(log);
  volundr.getSessions = async () =>
    [
      {
        id: 's-1',
        name: 'fix flaky test',
        status: 'running',
        model: 'claude',
        personaName: 'realm-valhalla',
      },
      { id: 's-2', name: 'other', status: 'completed', model: 'claude', personaName: 'someone' },
    ] as never;
  const personas = fakePersonas(log);
  await personas.createPersona({
    name: 'realm-valhalla',
    description: 'Keep valhalla humming.',
  } as never);
  return {
    'valkyrie.realms': realms,
    'ravn.residents': residents,
    'ravn.ravens': residents,
    'ravn.personas': personas,
    mimir,
    volundr,
  };
}

describe('RealmPage with a running resident', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('shows sessions, QA findings, the conversation and realm memory', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const { router } = renderRealms('/realms/valhalla', await populated(log), log);
    await screen.findByTestId('realm-page');
    await waitFor(() =>
      expect(screen.getByTestId('queue-findings')).toHaveTextContent('QA findings1'),
    );
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'New conversation' })).toBeInTheDocument(),
    );
    await user.click(screen.getByRole('button', { name: 'New conversation' }));
    await screen.findByText(/not used in tests/);
    await user.click(screen.getByRole('button', { name: /^Queue/ }));
    expect(await screen.findByText(/QA findings · 1/)).toBeInTheDocument();
    expect(screen.getAllByText(/LXA-1/).length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: /^Sessions/ }));
    expect(await screen.findByText(/Resident logs/)).toBeInTheDocument();
    expect(screen.getByText('fix flaky test')).toBeInTheDocument();
    expect(screen.queryByText('other')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Memory' }));
    const exploreLink = await screen.findByRole('link', { name: "Explore this realm's memory" });
    await user.click(exploreLink);
    await waitFor(() => expect(router.state.location.pathname).toBe('/mimir'));
    expect(router.state.location.search).toEqual({ mount: 'realm-valhalla' });
  });

  it('lets you decide a pending review and finish the walkthrough', async () => {
    const user = userEvent.setup();
    localStorage.setItem(
      'niuu.compactUx.walkthrough.first-realm',
      JSON.stringify({ done: ['template', 'connect', 'charter', 'trust'], hidden: false }),
    );
    const log = createCallLog();
    renderRealms('/realms/valhalla', await populated(log), log);
    await screen.findByTestId('realm-page');
    const finish = await screen.findByRole('button', { name: 'I answered its first question' });
    await user.click(finish);
    await screen.findByText('Done. Nicely kept.');
    expect(screen.getByText('What it did')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Hide walkthrough' }));
    expect(screen.queryByTestId('walkthrough-rail')).not.toBeInTheDocument();
    await user.click(screen.getByTestId('walkthrough-show'));
    expect(await screen.findByTestId('walkthrough-rail')).toBeInTheDocument();
  });

  it('picks a workflow before launching it into the realm', async () => {
    const user = userEvent.setup();
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'advanced');
    const log = createCallLog();
    renderRealms('/realms/valhalla', await populated(log), log);
    await screen.findByTestId('realm-page');
    await waitFor(() => expect(screen.getByTestId('realm-run-workflow')).toBeEnabled());
    await user.click(screen.getByTestId('realm-run-workflow'));
    const picker = await screen.findByTestId('workflow-picker');
    const first = picker.querySelector('button')!;
    await user.click(first);
    await waitFor(() => expect(screen.queryByTestId('workflow-picker')).not.toBeInTheDocument());
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('pauses and removes the resident from settings', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    renderRealms('/realms/valhalla/settings', await populated(log), log);
    await screen.findByTestId('realm-settings');
    await user.click(screen.getByRole('button', { name: 'Resident' }));
    await screen.findByTestId('resident-controls');
    await user.click(screen.getByRole('button', { name: 'Pause' }));
    await waitFor(() => expect(log.calls).toContain('applyLifecycle:valhalla:suspend'));
    await user.click(screen.getByTestId('resident-remove'));
    await user.click(screen.getByRole('button', { name: 'Keep it' }));
    expect(screen.getByTestId('resident-remove')).toBeInTheDocument();
    await user.click(screen.getByTestId('resident-remove'));
    await user.click(screen.getByTestId('resident-remove-confirm'));
    await waitFor(() => expect(log.calls).toContain('deleteResident:valhalla'));
  });

  it('deletes the realm in two clicks and lands on the home page', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const { router } = renderRealms('/realms/valhalla/settings', await populated(log), log);
    await screen.findByTestId('realm-settings');
    await user.click(screen.getByRole('button', { name: 'Resident' }));
    await screen.findByTestId('realm-teardown');
    await user.click(screen.getByTestId('realm-delete'));
    await user.click(screen.getByTestId('realm-delete-confirm'));
    await waitFor(() => expect(log.calls).toContain('deleteRealm:valhalla'));
    await waitFor(() => expect(router.state.location.pathname).toBe('/realms'));
    expect(log.calls).toContain('deletePersona:realm-valhalla');
  });

  it('clones the realm into the wizard with its charter and trust', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const { router } = renderRealms('/realms/valhalla', await populated(log), log);
    await screen.findByTestId('realm-page');
    await user.click(screen.getByRole('button', { name: 'Clone' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/realms/new'));
    await screen.findByTestId('wizard-step-connect');
    await user.click(screen.getByTestId('realm-continue'));
    await screen.findByTestId('wizard-step-charter');
    expect(screen.getByTestId('realm-charter')).toHaveValue('Keep valhalla humming.');
    await user.click(screen.getByRole('button', { name: 'Back' }));
    await screen.findByTestId('wizard-step-connect');
  });

  it('opens on the chosen template from the templates tab', async () => {
    renderRealms('/realms/new?template=qa-resident');
    const qa = await screen.findByTestId('template-qa-resident');
    expect(qa).toHaveAttribute('aria-pressed', 'true');
  });
});
