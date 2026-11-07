import { describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { createMockVolundrService } from '../adapters/mock';
import type { ForgeProject } from '../models/volundr.model';
import { renderWithVolundr } from '../testing/renderWithVolundr';
import { ProjectSettings } from './ProjectSettings';

const PROJECT: ForgeProject = {
  id: 'p-1',
  name: 'A2A Interoperability',
  slug: 'a2a',
  status: 'active',
  description: 'Agents talking',
  brief: 'Keep it small.',
  revision: 3,
  instance_id: 'valhalla',
  instance_name: 'Valhalla',
};

function setup(overrides: { updateProject?: ReturnType<typeof vi.fn> } = {}) {
  const service = createMockVolundrService();
  const updateProject =
    overrides.updateProject ??
    vi.fn(async (project: ForgeProject, changes) => ({
      ...project,
      ...changes,
      revision: (project.revision ?? 1) + 1,
    }));
  service.updateProject = updateProject;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  const onOpenChange = vi.fn();
  const onArchived = vi.fn();
  renderWithVolundr(
    <ProjectSettings project={PROJECT} open onOpenChange={onOpenChange} onArchived={onArchived} />,
    { service, queryClient },
  );
  return { updateProject, invalidate, onOpenChange, onArchived };
}

describe('ProjectSettings', () => {
  it('shows the project and the note about the brief, with Save off until something changes', () => {
    setup();
    expect(screen.getByRole('dialog', { name: 'Project settings' })).toBeInTheDocument();
    expect(screen.getByLabelText('Name')).toHaveValue('A2A Interoperability');
    expect(screen.getByLabelText('Description')).toHaveValue('Agents talking');
    expect(screen.getByLabelText('Brief')).toHaveValue('Keep it small.');
    expect(
      screen.getByText(/New coordinators and workers receive the brief when they start/),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    // The checkout lives on the host that owns the project.
    expect(screen.getByLabelText('Host')).toBeDisabled();
    expect(screen.getByLabelText('Host')).toHaveValue('Valhalla');
    expect(screen.getByRole('button', { name: 'Connect repository' })).toBeDisabled();
  });

  it('saves only the changed fields at the revision it read, then refreshes the projects', async () => {
    const { updateProject, invalidate } = setup();
    fireEvent.change(screen.getByLabelText('Brief'), { target: { value: 'New brief' } });
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(updateProject).toHaveBeenCalledTimes(1));
    expect(updateProject).toHaveBeenCalledWith(PROJECT, { description: '', brief: 'New brief' });
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ['volundr', 'projects'] }),
    );
    // Saved: nothing differs from what Forge holds, so Save is off again.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled());
    // A second change goes against the revision Forge answered with.
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: ' Renamed ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(updateProject).toHaveBeenCalledTimes(2));
    const [second, changes] = updateProject.mock.calls[1]!;
    expect(second.revision).toBe(4);
    expect(changes).toEqual({ name: 'Renamed' });
  });

  it('will not save an empty name', () => {
    setup();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: '   ' } });
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('connects a checkout by setting the workspace path, and says what it connected to', async () => {
    const { updateProject } = setup({
      updateProject: vi.fn(async (project: ForgeProject, changes) => ({
        ...project,
        ...changes,
        repo_url: 'github.com/niuulabs/a2a',
        revision: 4,
      })),
    });
    const connect = screen.getByRole('button', { name: 'Connect repository' });
    fireEvent.change(screen.getByLabelText('Git checkout folder'), {
      target: { value: ' /work/a2a ' },
    });
    expect(connect).toBeEnabled();
    fireEvent.click(connect);
    await waitFor(() =>
      expect(updateProject).toHaveBeenCalledWith(PROJECT, { workspace_path: '/work/a2a' }),
    );
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Connected to github.com/niuulabs/a2a',
    );
    expect(screen.getByLabelText('Git checkout folder')).toHaveValue('/work/a2a');
    expect(screen.getByRole('button', { name: 'Connect repository' })).toBeDisabled();
  });

  it('shows why a connection failed and leaves the form as it was', async () => {
    setup({
      updateProject: vi.fn(async () => {
        throw Object.assign(new Error('x'), {
          detail: "This folder's Git remote is not the project's repository",
        });
      }),
    });
    fireEvent.change(screen.getByLabelText('Git checkout folder'), {
      target: { value: '/work/other' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Connect repository' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "This folder's Git remote is not the project's repository",
    );
    expect(screen.getByLabelText('Git checkout folder')).toHaveValue('/work/other');
  });

  it('shows a failed save and keeps the edits', async () => {
    setup({
      updateProject: vi.fn(async () => {
        throw new Error('Project changed; reload before saving');
      }),
    });
    fireEvent.change(screen.getByLabelText('Brief'), { target: { value: 'Mine' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Project changed; reload before saving',
    );
    expect(screen.getByLabelText('Brief')).toHaveValue('Mine');
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
  });

  it('archives only after a second confirmation, then leaves the project', async () => {
    const { updateProject, onArchived } = setup();
    expect(
      screen.getByText(
        'Archiving hides the project. Its sessions keep running and stay in Sessions.',
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Archive project' }));
    expect(updateProject).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Keep project' }));
    expect(screen.getByRole('button', { name: 'Archive project' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Archive project' }));
    fireEvent.click(screen.getByRole('button', { name: 'Archive it' }));
    await waitFor(() => expect(onArchived).toHaveBeenCalledTimes(1));
    expect(updateProject).toHaveBeenCalledWith(PROJECT, { status: 'archived' });
  });

  it('shows a failed archive and does not leave the project', async () => {
    const { onArchived } = setup({
      updateProject: vi.fn(async () => {
        throw new Error('Forge is unreachable');
      }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Archive project' }));
    fireEvent.click(screen.getByRole('button', { name: 'Archive it' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Forge is unreachable');
    expect(onArchived).not.toHaveBeenCalled();
  });

  it('closes from Done without saving', () => {
    const { onOpenChange, updateProject } = setup();
    fireEvent.change(screen.getByLabelText('Brief'), { target: { value: 'Unsaved' } });
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(updateProject).not.toHaveBeenCalled();
  });

  it('names the host as "This host" when the project carries none', () => {
    const service = createMockVolundrService();
    renderWithVolundr(
      <ProjectSettings
        project={{ ...PROJECT, instance_id: undefined, instance_name: undefined }}
        open
        onOpenChange={vi.fn()}
        onArchived={vi.fn()}
      />,
      { service },
    );
    expect(screen.getByLabelText('Host')).toHaveValue('This host');
  });
});
