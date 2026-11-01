import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createSeedRealms } from '@niuulabs/plugin-valkyrie';
import {
  createCallLog,
  fakeRealmService,
  fakeVolundr,
  workspaceWithWaitingTicket,
} from '../testing/fakes';
import { renderRealms } from '../testing/renderRealms';

describe('RealmsHomePage', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('lists every realm as a card with its resident state', async () => {
    renderRealms('/realms');
    await screen.findByTestId('realm-card-valhalla');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      /workspaces, each kept by a resident/,
    );
  });

  it('groups workspaces under their project, the ones without a project last', async () => {
    const log = createCallLog();
    const volundr = fakeVolundr(log);
    renderRealms('/realms', { volundr }, log);
    const group = await screen.findByTestId('project-group-project-yggdrasil');
    await screen.findByRole('link', { name: 'Yggdrasil' });
    expect(within(group).getByTestId('realm-card-valhalla')).toBeInTheDocument();
    expect(within(group).getByTestId('realm-card-printer-forge')).toBeInTheDocument();
    const standalone = screen.getByTestId('project-group-none');
    expect(within(standalone).getByTestId('realm-card-host-jozef')).toBeInTheDocument();
    expect(within(standalone).getByText('Not in a project')).toBeInTheDocument();
    const groups = screen.getByTestId('realm-cards').children;
    expect(groups[groups.length - 1]).toBe(standalone);
  });

  it('opens straight into the workspace of a project that has only one', async () => {
    const log = createCallLog();
    const [valhalla, ...others] = createSeedRealms();
    const realms = [
      { ...valhalla!, project_id: 'project-solo' },
      ...others.map((realm) => ({ ...realm, project_id: null })),
    ];
    renderRealms('/realms', { 'valkyrie.realms': fakeRealmService(log, realms) }, log);
    const group = await screen.findByTestId('project-group-project-solo');
    expect(within(group).getByRole('heading', { level: 2 }).querySelector('a')).toHaveAttribute(
      'href',
      '/realms/valhalla',
    );
  });

  it('gives a running resident a workspace without deploying anything', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const { router } = renderRealms(
      '/realms',
      { 'valkyrie.realms': fakeRealmService(log, []) },
      log,
    );
    const list = await screen.findByTestId('unclaimed-residents');
    const first = within(list).getAllByRole('button', { name: 'Create workspace' })[0]!;
    await user.click(first);
    const dialog = await screen.findByTestId('adopt-dialog');
    const name = within(dialog).getByRole('textbox', { name: 'Workspace name' });
    await user.clear(name);
    await user.type(name, 'Clusters');
    await user.click(screen.getAllByRole('button', { name: 'Create workspace' }).at(-1)!);
    await waitFor(() => expect(log.calls.some((c) => c.startsWith('createRealm:'))).toBe(true));
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/realms\/[a-z0-9]/));
    expect(log.calls.some((call) => call.startsWith('deploy'))).toBe(false);
  });

  it('shows the empty state when there are no realms', async () => {
    const log = createCallLog();
    renderRealms('/realms', { 'valkyrie.realms': fakeRealmService(log, []) }, log);
    await screen.findByText('No workspaces yet');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Start with one sentence');
  });

  it('turns a sentence into a draft realm', async () => {
    const user = userEvent.setup();
    const { router } = renderRealms('/realms');
    await screen.findByTestId('sentence-composer');
    expect(screen.getByTestId('sentence-submit')).toBeDisabled();
    await user.type(screen.getByTestId('sentence-input'), 'Keep niuulabs/lexi-api shippable');
    await user.click(screen.getByTestId('sentence-submit'));
    await waitFor(() => expect(router.state.location.pathname).toBe('/realms/new'));
    expect(router.state.location.search).toMatchObject({
      sentence: 'Keep niuulabs/lexi-api shippable',
    });
  });

  it('lists the templates on the templates tab', async () => {
    renderRealms('/realms/templates');
    await screen.findByText('Product resident');
    expect(screen.getAllByText('Use this template')).toHaveLength(4);
  });

  it('lets you decide a pending review from the needs-you tab', async () => {
    const user = userEvent.setup();
    renderRealms('/realms/needs-you');
    const approve = (await screen.findAllByRole('button', { name: 'Approve' }))[0]!;
    await user.click(approve);
    await waitFor(() =>
      expect(screen.queryAllByRole('button', { name: 'Approve' }).length).toBeLessThan(10),
    );
  });

  it('lists a ticket at a gate in the inbox and in the panel', async () => {
    const log = createCallLog();
    const services = await workspaceWithWaitingTicket(log, 'valhalla');
    const inbox = renderRealms('/realms/needs-you', services, log);
    expect(await screen.findByTestId('needs-you-ticket-run-1')).toHaveTextContent('Valhalla');
    inbox.unmount();

    renderRealms('/realms', services, log);
    expect(await screen.findByTestId('needs-you-ticket-run-1')).toBeInTheDocument();
    expect(screen.getByText('1 open ·', { exact: false })).toBeInTheDocument();
  });

  it('opens the clone picker and routes to the wizard with the source realm', async () => {
    const user = userEvent.setup();
    const { router } = renderRealms('/realms');
    await screen.findByTestId('realm-card-valhalla');
    await user.click(screen.getByRole('button', { name: 'Clone a workspace' }));
    const picker = await screen.findByTestId('clone-picker');
    await user.click(picker.querySelector('button')!);
    await waitFor(() => expect(router.state.location.pathname).toBe('/realms/new'));
    expect(router.state.location.search).toMatchObject({ from: 'valhalla' });
  });
});
