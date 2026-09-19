import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import { AssignSessionProject } from './AssignSessionProject';

function setup({ assigned = false, coordinator = false, unsupported = false } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(
    ['volundr', 'domain-sessions', null],
    [
      { id: 'worker', clusterId: 'thor', name: 'Worker' },
      { id: 'worker', clusterId: 'build', name: 'Other host session' },
    ],
  );
  const getSessionProject = vi.fn().mockImplementation(async () => {
    if (unsupported) throw new Error('Update this Forge host to enable project assignment');
    return {
      sessionId: 'worker',
      revision: 3,
      projectId: assigned ? 'lexi' : null,
      role: coordinator ? 'coordinator' : 'worker',
    };
  });
  const getProjects = vi.fn().mockImplementation(async ({ instanceId }: { instanceId: string }) => {
    if (instanceId === 'offline') throw new Error('Host unavailable');
    return [
      { id: 'kit', name: 'Kit', slug: 'kit', status: 'active' },
      { id: 'old', name: 'Old', slug: 'old', status: 'archived' },
    ];
  });
  const assignSessionProject = vi
    .fn()
    .mockResolvedValue({ sessionId: 'worker', revision: 4, projectId: 'kit', role: 'worker' });
  const getTargets = vi.fn().mockResolvedValue([
    { id: 'build', name: 'Build' },
    { id: 'offline', name: 'Offline' },
  ]);
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          volundr: { getSessionProject, getProjects, assignSessionProject, getTargets },
          sessionStore: { listRequestTimeoutMs: 100 },
        }}
      >
        <AssignSessionProject sessionId="worker" instanceId="thor" name="Worker" />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  const open = () =>
    fireEvent.click(screen.getByRole('button', { name: 'Assign project for Worker' }));
  return { client, getSessionProject, getProjects, assignSessionProject, getTargets, open };
}

describe('AssignSessionProject', () => {
  it('loads only when opened, assigns a foreign project and updates only the owning row', async () => {
    const { client, getTargets, assignSessionProject, open } = setup();
    expect(getTargets).not.toHaveBeenCalled();
    open();
    const select = screen.getByRole('combobox', { name: 'Project' });
    await screen.findByRole('option', { name: 'Kit · Build' });
    expect(screen.queryByRole('option', { name: /Old/ })).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Offline');
    fireEvent.change(select, { target: { value: 'build:kit' } });
    fireEvent.click(screen.getByRole('button', { name: 'Assign', exact: true }));
    await waitFor(() => expect(screen.queryByRole('combobox')).not.toBeInTheDocument());
    expect(assignSessionProject).toHaveBeenCalledExactlyOnceWith(
      'worker',
      {
        projectId: 'kit',
        projectInstanceId: 'build',
        expectedRevision: 3,
      },
      { instanceId: 'thor' },
    );
    expect(client.getQueryData(['volundr', 'domain-sessions', null])).toEqual([
      {
        id: 'worker',
        clusterId: 'thor',
        name: 'Worker',
        coordination: { projectId: 'kit', role: 'worker', parent: null },
      },
      { id: 'worker', clusterId: 'build', name: 'Other host session' },
    ]);
  });

  it('explains moving and retains a failed draft until the user reloads', async () => {
    const { assignSessionProject, getSessionProject, open } = setup({ assigned: true });
    assignSessionProject.mockRejectedValueOnce(
      new Error('Session project changed; reload before assigning'),
    );
    open();
    await screen.findByRole('option', { name: 'Kit · Build' });
    expect(screen.getByText(/removes this session’s current coordinator/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'build:kit' } });
    fireEvent.click(screen.getByRole('button', { name: 'Move', exact: true }));
    await screen.findByText('Session project changed; reload before assigning');
    expect(screen.getByRole('combobox')).toHaveValue('build:kit');
    getSessionProject.mockResolvedValue({
      sessionId: 'worker',
      revision: 4,
      projectId: 'lexi',
      role: 'worker',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
    await waitFor(() =>
      expect(
        screen.queryByText('Session project changed; reload before assigning'),
      ).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(assignSessionProject).toHaveBeenCalledTimes(1);
  });

  it('shows loading, cancels without a write and preserves a pending save', async () => {
    const { getProjects, getTargets, assignSessionProject, open } = setup();
    getTargets.mockResolvedValue([{ id: 'build', name: 'Build' }]);
    let resolveProjects!: (value: unknown) => void;
    getProjects.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveProjects = resolve;
        }),
    );
    open();
    await screen.findByText('Loading projects and session…');
    expect(screen.getByRole('button', { name: 'Assign', exact: true })).toBeDisabled();
    await waitFor(() => expect(getProjects).toHaveBeenCalledTimes(1));
    await act(async () => resolveProjects([{ id: 'kit', name: 'Kit', status: 'active' }]));
    await screen.findByRole('option', { name: 'Kit · Build' });
    let finish!: (value: unknown) => void;
    assignSessionProject.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'build:kit' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Assign session project' }));
    await screen.findByRole('button', { name: 'Saving…' });
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    fireEvent.submit(screen.getByRole('form', { name: 'Assign session project' }));
    expect(assignSessionProject).toHaveBeenCalledTimes(1);
    await act(async () =>
      finish({ sessionId: 'worker', revision: 4, projectId: 'kit', role: 'worker' }),
    );
    await waitFor(() => expect(screen.queryByRole('combobox')).not.toBeInTheDocument());
  });

  it.each([{ coordinator: true }, { unsupported: true }])(
    'disables unsupported assignments: %j',
    async (options) => {
      const { assignSessionProject, open } = setup(options);
      open();
      await screen.findByRole('option', { name: 'Kit · Build' });
      if (options.coordinator) await screen.findByText(/Create a coordinator/);
      else await screen.findByText(/Update this Forge host/);
      expect(screen.getByRole('button', { name: 'Assign', exact: true })).toBeDisabled();
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(assignSessionProject).not.toHaveBeenCalled();
    },
  );
});
