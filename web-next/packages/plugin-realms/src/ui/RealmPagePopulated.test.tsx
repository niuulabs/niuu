import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    await waitFor(() => expect(screen.getByText('fix flaky test')).toBeInTheDocument());
    expect(screen.queryByText('other')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/QA findings · 1/)).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'New conversation' })).toBeInTheDocument(),
    );
    await user.click(screen.getByRole('button', { name: 'New conversation' }));
    await screen.findByText(/not used in tests/);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Open realm memory' })).toBeEnabled(),
    );
    await user.click(screen.getByRole('button', { name: 'Open realm memory' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/mimir/pages'));
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
    expect(screen.getByText('Needs you')).toBeInTheDocument();
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
