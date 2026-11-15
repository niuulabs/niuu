import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMockTingService, createMockWorkService } from '@niuulabs/plugin-ting';
import { createSeedRealms } from '../adapters/mock';
import { createCallLog, fakeMimir, fakeRealmService, fakeVolundr } from '../testing/fakes';
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
      },
      { id: 's-2', name: 'other', status: 'completed', model: 'claude' },
    ] as never;
  return {
    'realms.governance': realms,
    // The board's one ticket is coordinated by session s-1, which is what makes it the
    // workspace's session.
    ting: {
      ...createMockTingService(),
      getSagas: async () => [{ id: 'link-1', trackerId: 'board-1', name: 'Board' }],
    },
    'ting.work': {
      ...createMockWorkService(),
      get: async () => ({
        board: null,
        tasks: [
          {
            source: { issueId: 'i-1', identifier: 'LXA-1', url: '' },
            title: 'First ticket',
            coordinator: { sessionId: 's-1' },
          },
        ],
        coordinators: [],
        coverage: [],
      }),
    },
    mimir,
    volundr,
  };
}

describe('RealmPage of a populated workspace', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('shows sessions, QA findings and workspace memory', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const { router } = renderRealms('/realms/valhalla', await populated(log), log);
    await screen.findByTestId('realm-page');
    await waitFor(() =>
      expect(screen.getByTestId('queue-findings')).toHaveTextContent('QA findings1'),
    );
    await user.click(screen.getByRole('button', { name: /^Board/ }));
    expect(await screen.findByText(/QA findings · 1/)).toBeInTheDocument();
    expect(screen.getAllByText(/LXA-1/).length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: /^Sessions/ }));
    expect(await screen.findByText('fix flaky test')).toBeInTheDocument();
    expect(screen.queryByText('other')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Memory' }));
    const exploreLink = await screen.findByRole('link', {
      name: "Explore this workspace's memory",
    });
    await user.click(exploreLink);
    await waitFor(() => expect(router.state.location.pathname).toBe('/mimir'));
    expect(router.state.location.search).toEqual({ mount: 'realm-valhalla' });
  });

  it('hides the walkthrough and brings it back', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    renderRealms('/realms/valhalla', await populated(log), log);
    await screen.findByTestId('realm-page');
    await user.click(await screen.findByRole('button', { name: 'Hide walkthrough' }));
    expect(screen.queryByTestId('walkthrough-rail')).not.toBeInTheDocument();
    await user.click(screen.getByTestId('walkthrough-show'));
    expect(await screen.findByTestId('walkthrough-rail')).toBeInTheDocument();
  });

  it('picks a workflow before launching it into the realm', async () => {
    const user = userEvent.setup();
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

  it('deletes the realm in two clicks and lands on the home page', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const { router } = renderRealms('/realms/valhalla?tab=settings', await populated(log), log);
    await screen.findByTestId('realm-teardown');
    await user.click(screen.getByTestId('realm-delete'));
    await user.click(screen.getByTestId('realm-delete-confirm'));
    await waitFor(() => expect(log.calls).toContain('deleteRealm:valhalla'));
    await waitFor(() => expect(router.state.location.pathname).toBe('/realms'));
    expect(log.calls.some((call) => call.startsWith('deleteResident'))).toBe(false);
  });

  it('clones the realm into the wizard with its template and trust', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const { router } = renderRealms('/realms/valhalla', await populated(log), log);
    await screen.findByTestId('realm-page');
    await user.click(screen.getByRole('button', { name: 'Clone' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/realms/new'));
    await screen.findByTestId('wizard-step-connect');
    await user.click(screen.getByTestId('realm-continue'));
    await screen.findByTestId('wizard-step-charter');
    expect(screen.getByTestId('realm-charter')).toHaveValue('');
    expect(screen.getByRole('group', { name: 'deploy trust' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Back' }));
    await screen.findByTestId('wizard-step-connect');
  });

  it('opens on the chosen template from the templates tab', async () => {
    renderRealms('/realms/new?template=qa-resident');
    const qa = await screen.findByTestId('template-qa-resident');
    expect(qa).toHaveAttribute('aria-pressed', 'true');
  });
});
