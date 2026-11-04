import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMockOdinReviewService, createSeedRealms } from '@niuulabs/plugin-valkyrie';
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
      /workspaces across your projects, each kept by a resident/,
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

  it('folds a project away, keeps what needs you in view and remembers it', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const services = await workspaceWithWaitingTicket(log, 'valhalla');
    const first = renderRealms('/realms', services, log);
    const group = await screen.findByTestId('project-group-project-yggdrasil');
    await within(group).findByRole('link', { name: 'Review 1' });

    await user.click(within(group).getByRole('button', { name: /^Collapse / }));

    expect(within(group).queryByTestId('realm-card-valhalla')).not.toBeInTheDocument();
    expect(within(group).getByRole('button', { name: /^Expand / })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    expect(group).toHaveTextContent('1 needs you');
    expect(screen.getByTestId('project-group-none')).toContainElement(
      screen.getByTestId('realm-card-host-jozef'),
    );

    first.unmount();
    renderRealms('/realms', services, log);
    const again = await screen.findByTestId('project-group-project-yggdrasil');
    expect(within(again).queryByTestId('realm-card-valhalla')).not.toBeInTheDocument();

    await user.click(within(again).getByRole('button', { name: /^Expand / }));
    expect(within(again).getByTestId('realm-card-valhalla')).toBeInTheDocument();
    expect(again).not.toHaveTextContent('needs you ');
  });

  it('folds the workspaces without a project on their own', async () => {
    const user = userEvent.setup();
    renderRealms('/realms');
    const standalone = await screen.findByTestId('project-group-none');
    await user.click(within(standalone).getByRole('button', { name: 'Collapse Not in a project' }));
    expect(screen.queryByTestId('realm-card-host-jozef')).not.toBeInTheDocument();
    expect(screen.getByTestId('realm-card-valhalla')).toBeInTheDocument();
  });

  it('creates a project and opens it to add workspaces', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const { router } = renderRealms('/realms', {}, log);
    await screen.findByTestId('realm-cards');
    await user.click(screen.getByRole('button', { name: 'New project' }));
    const dialog = await screen.findByTestId('new-project-dialog');
    expect(screen.getByRole('button', { name: 'Create project' })).toBeDisabled();

    await user.type(
      within(dialog).getByRole('textbox', { name: 'Project name' }),
      ' Home lab {Enter}',
    );

    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/realms/projects/project-new'),
    );
    expect(log.calls.filter((call) => call.startsWith('createProject'))).toEqual([
      'createProject:Home lab',
    ]);
  });

  it('says why a project could not be created', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const volundr = fakeVolundr(log);
    vi.spyOn(volundr, 'createProject').mockRejectedValue(new Error('forge said no'));
    const { router } = renderRealms('/realms', { volundr }, log);
    await screen.findByTestId('realm-cards');
    await user.click(screen.getByRole('button', { name: 'New project' }));
    const dialog = await screen.findByTestId('new-project-dialog');
    await user.type(within(dialog).getByRole('textbox', { name: 'Project name' }), 'Home lab');
    await user.click(screen.getByRole('button', { name: 'Create project' }));
    expect(await within(dialog).findByText(/forge said no/)).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/realms');
  });

  it('keeps a project with no workspaces reachable', async () => {
    const log = createCallLog();
    const volundr = fakeVolundr(log);
    const projects = await volundr.getProjects();
    vi.spyOn(volundr, 'getProjects').mockResolvedValue([
      ...projects,
      { id: 'project-spring', name: 'Spring catalogue', slug: 'spring', status: 'active' },
    ]);
    renderRealms('/realms', { volundr }, log);
    const row = await screen.findByTestId('empty-projects');
    expect(within(row).getByRole('link', { name: 'Spring catalogue' })).toHaveAttribute(
      'href',
      '/realms/projects/project-spring',
    );
  });

  it('counts the whole review queue, not the page of it that was listed', async () => {
    const reviews = createMockOdinReviewService();
    const summary = await reviews.getSummary();
    const listed = (await reviews.listReviews({ status: 'pending' })).slice(0, 2);
    vi.spyOn(reviews, 'listReviews').mockResolvedValue(listed);
    vi.spyOn(reviews, 'getSummary').mockResolvedValue({
      ...summary,
      pendingTotal: 340,
      pendingByEnvironment: { 'env-k8s-valhalla': 330 },
    });
    const home = renderRealms('/realms', { 'valkyrie.reviews': reviews });
    expect(await screen.findByText('340 open ·', { exact: false })).toBeInTheDocument();
    await within(await screen.findByTestId('realm-card-valhalla')).findByRole('link', {
      name: 'Review 330',
    });
    home.unmount();

    renderRealms('/realms/needs-you', { 'valkyrie.reviews': reviews });
    expect(await screen.findByTestId('needs-you-truncated')).toHaveTextContent(
      'Showing the newest 2 of 340 open reviews.',
    );
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
    await screen.findByText('No projects yet');
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
