import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMockRunLogService } from '@niuulabs/plugin-ting';
import { createCallLog, workspaceWithWaitingTicket } from '../testing/fakes';
import { WITHOUT_TING_PLUGIN, renderRealms } from '../testing/renderRealms';

const PROJECT = '/realms/projects/project-yggdrasil';

/** One Forge node whose UI shows the Guild's Ting, with the Ting plugin off. */
async function renderNode(path: string, overrides: Record<string, unknown> = {}) {
  const log = createCallLog();
  const result = renderRealms(
    path,
    {
      ...(await workspaceWithWaitingTicket(log, 'valhalla')),
      'ting.runLog': {
        ...createMockRunLogService(),
        scope: 'node',
        hosts: async () => ['this-node'],
        session: async () => null,
        publications: async () => [],
        projectSessions: async () => [],
      },
      ...overrides,
    },
    log,
    WITHOUT_TING_PLUGIN,
  );
  return result;
}

function expectNoTingLinks() {
  for (const link of screen.queryAllByRole('link')) {
    expect(link.getAttribute('href')).not.toMatch(/^\/ting/);
  }
}

describe("a node that shows another host's Ting, with the Ting plugin off", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it.each(['/realms', '/realms/needs-you', PROJECT, '/home'])(
    'shows what waits on %s and reviews it on the workspace board',
    async (path) => {
      const { router } = await renderNode(path);

      const row = await screen.findByTestId('needs-you-ticket-run-1');
      expect(screen.queryByTestId('needs-you-error')).not.toBeInTheDocument();
      expectNoTingLinks();

      await userEvent.click(within(row).getByRole('link', { name: 'Review' }));
      await waitFor(() => expect(router.state.location.pathname).toBe('/realms/valhalla'));
      expect(router.state.location.search).toEqual({ tab: 'board' });
      expect(await screen.findByRole('button', { name: /^Board/ })).toHaveAttribute(
        'aria-pressed',
        'true',
      );
    },
  );

  it('says its session counts are its own, for work that may run anywhere', async () => {
    await renderNode('/realms');
    const card = await screen.findByTestId('realm-card-valhalla');
    expect(within(card).getByText('in sessions here')).toBeInTheDocument();

    cleanup();
    await renderNode('/realms/valhalla');
    expect(await screen.findByTestId('queue-sessions')).toHaveTextContent('0 on this host');
  });

  it("opens a workspace's board for what waits on it, without leaving the page", async () => {
    await renderNode('/realms/valhalla');
    const strip = await screen.findByTestId('realm-needs-you');
    await userEvent.click(within(strip).getByRole('button', { name: 'Review' }));
    expect(screen.getByRole('button', { name: /^Board/ })).toHaveAttribute('aria-pressed', 'true');
    expectNoTingLinks();
  });

  it('runs a workflow from a workspace without a way to the builder it does not have', async () => {
    await renderNode('/realms/valhalla', {
      'ting.workflows': { listWorkflows: vi.fn().mockResolvedValue([]) },
    });
    const run = await screen.findByTestId('realm-run-workflow');
    await waitFor(() => expect(run).toBeEnabled());
    await userEvent.click(run);
    expect(await screen.findByText('No workflows yet')).toBeInTheDocument();
    expectNoTingLinks();
  });

  it('offers no workflow on Home, whose pages are not here', async () => {
    await renderNode('/home');
    await screen.findByTestId('home-choice-session');
    expect(screen.queryByTestId('home-choice-workflow')).not.toBeInTheDocument();
  });
});
