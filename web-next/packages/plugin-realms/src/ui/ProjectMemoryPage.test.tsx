import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMockVolundrService, type IVolundrService } from '@niuulabs/plugin-volundr';
import {
  MEMORY_IMPORTS_SERVICE_KEY,
  createMockMemoryImportsService,
  type IMemoryImportsService,
} from '@niuulabs/plugin-ting';
import { renderRealms } from '../testing/renderRealms';
import { createCallLog, fakeTracker } from '../testing/fakes';
import { mountNameFor, parsePrefixes } from '../application/useProjectMemory';

const PAGE = '/realms/projects/project-yggdrasil/memory';
const LINEAR = {
  id: 'conn-linear',
  slug: 'linear',
  integration_type: 'issue_tracker',
  config: {},
  enabled: true,
  connected_by: 'u1',
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
};

function volundrWith(overrides: Partial<IVolundrService> = {}): IVolundrService {
  const base = createMockVolundrService();
  return {
    ...base,
    getTeamIntegrations: async () => [LINEAR],
    ...overrides,
  };
}

function render(volundr: IVolundrService, imports?: IMemoryImportsService) {
  const log = createCallLog();
  return renderRealms(
    PAGE,
    {
      volundr,
      [MEMORY_IMPORTS_SERVICE_KEY]: imports ?? createMockMemoryImportsService(),
      'ting.tracker': fakeTracker(log),
    },
    log,
  );
}

describe('ProjectMemoryPage', () => {
  afterEach(cleanup);

  it('shows what the project reads, where agents write, and the ticket switch', async () => {
    render(volundrWith());
    await screen.findByTestId('project-memory-page');
    expect(screen.getByRole('heading', { name: 'Memory' })).toBeInTheDocument();
    expect(await screen.findByText(/The org's memory/)).toBeInTheDocument();
    const org = screen.getByRole('checkbox', { name: /The org's memory/ });
    expect(org).toBeChecked();
    expect(org).toBeDisabled();
    expect(screen.getByRole('textbox', { name: 'Folders agents may write to' })).toHaveValue('');
    expect(await screen.findByRole('checkbox', { name: /Linear/ })).not.toBeChecked();
  });

  it('saves attached memories, write prefixes and a Linear import together', async () => {
    const user = userEvent.setup();
    const volundr = volundrWith();
    const add = vi.spyOn(volundr, 'addProjectMemoryMount');
    const update = vi.spyOn(volundr, 'updateProject');
    const imports = createMockMemoryImportsService();
    const create = vi.spyOn(imports, 'create');
    render(volundr, imports);
    await screen.findByTestId('project-memory-page');
    const [firstMemory] = (await screen.findAllByRole('checkbox')).filter(
      (box) =>
        !(box as HTMLInputElement).disabled &&
        !/Linear/.test(box.closest('label')?.textContent ?? ''),
    );
    await user.click(firstMemory!);
    const prefixes = screen.getByRole('textbox', { name: 'Folders agents may write to' });
    await user.type(prefixes, 'decisions/, runbooks/');
    await user.click(screen.getByRole('checkbox', { name: /Linear/ }));
    expect(await screen.findByRole('combobox', { name: 'Linear project' })).toHaveValue('board-1');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.');
    expect(add).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'project-yggdrasil' }),
      expect.objectContaining({ access: 'read' }),
    );
    expect(update).toHaveBeenCalledWith(expect.objectContaining({ id: 'project-yggdrasil' }), {
      memory_write_prefixes: ['decisions/', 'runbooks/'],
    });
    expect(create).toHaveBeenCalledWith({
      projectId: 'project-yggdrasil',
      connectionId: 'conn-linear',
      trackerProjectId: 'board-1',
      compile: true,
    });
    expect(await screen.findByRole('checkbox', { name: /Linear · Lexi API/ })).toBeChecked();
  });

  it('detaches a memory and stops an import', async () => {
    const user = userEvent.setup();
    const volundr = volundrWith();
    const project = (await volundr.getProjects()).find((p) => p.id === 'project-yggdrasil')!;
    await volundr.addProjectMemoryMount(project, {
      name: 'old-notes',
      registryEntryId: 'gone-entry',
      access: 'read',
    });
    const remove = vi.spyOn(volundr, 'removeProjectMemoryMount');
    const imports = createMockMemoryImportsService();
    await imports.create({
      projectId: 'project-yggdrasil',
      connectionId: 'conn-linear',
      trackerProjectId: 'board-1',
      compile: true,
    });
    const stop = vi.spyOn(imports, 'remove');
    render(volundr, imports);
    const unlisted = await screen.findByRole('checkbox', { name: 'old-notes' });
    expect(unlisted).toBeChecked();
    await user.click(unlisted);
    await user.click(await screen.findByRole('checkbox', { name: /Linear · Lexi API/ }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(stop).toHaveBeenCalledWith('import-1'));
    expect(remove).toHaveBeenCalledWith(expect.anything(), 'old-notes');
  });

  it('asks for the Linear project id when no tracker project is listed', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    renderRealms(
      PAGE,
      {
        volundr: volundrWith(),
        [MEMORY_IMPORTS_SERVICE_KEY]: createMockMemoryImportsService(),
        'ting.tracker': { ...fakeTracker(log), listProjects: async () => [] },
      },
      log,
    );
    await user.click(await screen.findByRole('checkbox', { name: /Linear/ }));
    const id = await screen.findByRole('textbox', { name: 'Linear project' });
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.type(id, 'lin-42');
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
  });

  it('shows a failed save', async () => {
    const user = userEvent.setup();
    render(
      volundrWith({
        updateProject: async () => {
          throw new Error('Project changed; reload before saving');
        },
      }),
    );
    await user.type(
      await screen.findByRole('textbox', { name: 'Folders agents may write to' }),
      'notes/',
    );
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('reload before saving');
  });

  it('tells a project without Linear how to bring tickets in', async () => {
    render(volundrWith({ getTeamIntegrations: async () => [] }));
    expect(await screen.findByText(/Connect Linear in the project/)).toBeInTheDocument();
  });

  it('is read-only for someone who is not an owner', async () => {
    const base = volundrWith();
    render({
      ...base,
      getIdentity: async () => ({ ...(await base.getIdentity()), userId: 'alice' }),
    });
    expect(await screen.findByText(/Only the project's owners change these/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Folders agents may write to' })).toBeDisabled();
  });

  it('says when the project is not there or cannot be loaded', async () => {
    const log = createCallLog();
    renderRealms('/realms/projects/nope/memory', { volundr: volundrWith() }, log);
    expect(await screen.findByText('No such project')).toBeInTheDocument();
    cleanup();
    render(
      volundrWith({
        getProjectMemoryMounts: async () => {
          throw new Error('Forge is down');
        },
      }),
    );
    expect(await screen.findByText(/Forge is down/)).toBeInTheDocument();
  });
});

describe('project memory helpers', () => {
  it('names a mount after its entry and reads prefixes from what was typed', () => {
    expect(mountNameFor('Platform team notes!')).toBe('platform-team-notes');
    expect(mountNameFor('***')).toBe('memory');
    expect(parsePrefixes(' decisions/, runbooks/  plans/,')).toEqual([
      'decisions/',
      'runbooks/',
      'plans/',
    ]);
  });
});
