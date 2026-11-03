import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createCallLog, fakeVolundr, workspaceWithWaitingTicket } from '../testing/fakes';
import { renderRealms } from '../testing/renderRealms';

describe('ProjectPage', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('shows the workspaces of the project and what they share', async () => {
    renderRealms('/realms/projects/project-yggdrasil');
    await screen.findByTestId('project-page');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Yggdrasil');
    expect(await screen.findByTestId('realm-card-valhalla')).toBeInTheDocument();
    expect(screen.getByTestId('realm-card-printer-forge')).toBeInTheDocument();
    expect(screen.queryByTestId('realm-card-host-jozef')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Project brief' })).toHaveValue(
      'The residents that keep the clusters.',
    );
  });

  it('saves the brief and the repository on the project', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    renderRealms('/realms/projects/project-yggdrasil', { volundr: fakeVolundr(log) }, log);
    const brief = await screen.findByRole('textbox', { name: 'Project brief' });
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.type(brief, ' And the mesh.');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(log.calls).toContain('updateProject:project-yggdrasil:brief,repo_url'),
    );
  });

  it('starts a new workspace in this project', async () => {
    const user = userEvent.setup();
    const { router } = renderRealms('/realms/projects/project-yggdrasil');
    await screen.findByTestId('project-page');
    await user.click(screen.getByRole('link', { name: 'New workspace' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/realms/new'));
    expect(router.state.location.search).toMatchObject({ project: 'project-yggdrasil' });
  });

  it('nests the sessions of the project under the one that started them', async () => {
    const log = createCallLog();
    const volundr = fakeVolundr(log);
    const member = (id: string, parent?: string) => ({
      id,
      name: id,
      status: 'running',
      model: 'm',
      coordination: {
        projectId: 'project-yggdrasil',
        role: parent ? 'worker' : 'coordinator',
        ...(parent ? { parent: { sessionId: parent, instanceId: 'x' } } : {}),
      },
    });
    volundr.getSessions = async () =>
      [
        member('worker-1', 'lead-1'),
        member('lead-1'),
        { id: 'elsewhere', name: 'elsewhere', status: 'running', model: 'm' },
      ] as never;
    renderRealms('/realms/projects/project-yggdrasil', { volundr }, log);
    expect(await screen.findByText('Sessions · 2')).toBeInTheDocument();
    expect(await screen.findByTestId('session-depth-1')).toHaveTextContent('worker-1');
    expect(screen.getByTestId('session-depth-0')).toHaveTextContent('lead-1');
    expect(screen.queryByText('elsewhere')).not.toBeInTheDocument();
  });

  it('shows a ticket at a gate on a workspace that has no resident asking', async () => {
    const log = createCallLog();
    const volundr = fakeVolundr(log);
    volundr.getSessions = async () =>
      [
        {
          id: 'lead',
          name: 'lead',
          status: 'running',
          model: 'm',
          coordination: { projectId: 'p', role: 'coordinator', labels: ['work-project:link-1'] },
        },
        {
          id: 'worker',
          name: 'worker',
          status: 'running',
          model: 'm',
          coordination: {
            projectId: 'p',
            role: 'worker',
            parent: { sessionId: 'lead', instanceId: 'x' },
          },
        },
      ] as never;
    const services = await workspaceWithWaitingTicket(log, 'valhalla');
    renderRealms('/realms/projects/project-yggdrasil', { ...services, volundr }, log);

    expect(await screen.findByText('Needs you · 1')).toBeInTheDocument();
    const row = await screen.findByTestId('needs-you-ticket-run-1');
    expect(row).toHaveTextContent('Valhalla · NIU-1 · Review the result');
    expect(within(row).getByRole('link', { name: 'Review' })).toHaveAttribute(
      'href',
      '/ting/review/link-1/issue-1',
    );
    const card = within(screen.getByTestId('realm-card-valhalla'));
    await waitFor(() => expect(card.getByText('Review 1')).toBeInTheDocument());
    expect(card.getByText('board Spring board')).toBeInTheDocument();
    expect(card.getByText('2')).toBeInTheDocument();
    expect(card.getByText('Review 1')).toHaveAttribute('href', '/realms/valhalla?tab=board');
  });

  it('says so when what is waiting could not be read', async () => {
    const log = createCallLog();
    const services = await workspaceWithWaitingTicket(log, 'valhalla');
    const work = {
      ...services['ting.work'],
      reviews: async () => {
        throw new Error('Work is down');
      },
    };
    renderRealms('/realms/projects/project-yggdrasil', { ...services, 'ting.work': work }, log);
    expect(await screen.findByTestId('needs-you-error')).toHaveTextContent('Work is down');
  });

  it('reports a project list that could not load', async () => {
    const log = createCallLog();
    const volundr = fakeVolundr(log);
    volundr.getProjects = async () => {
      throw new Error('no Forge answered');
    };
    renderRealms('/realms/projects/project-yggdrasil', { volundr }, log);
    expect(await screen.findByText('Could not load the project')).toBeInTheDocument();
  });

  it('explains a project nobody can find', async () => {
    renderRealms('/realms/projects/nope');
    expect(await screen.findByText('No such project')).toBeInTheDocument();
  });

  it('shows a project with no workspaces yet', async () => {
    const log = createCallLog();
    const volundr = fakeVolundr(log);
    volundr.getProjects = async () => [
      { id: 'empty', name: 'Empty', slug: 'empty', status: 'active', revision: 1 },
    ];
    renderRealms('/realms/projects/empty', { volundr }, log);
    expect(await screen.findByText('No workspaces in this project yet')).toBeInTheDocument();
  });
});
