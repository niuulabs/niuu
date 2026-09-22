import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import type {
  IDispatchBus,
  ITrackerBrowserService,
  IWorkflowService,
  Saga,
  TrackerProject,
} from '../ports';
import type { WorkSummary } from '../domain/work';
import type { Workflow } from '../domain/workflow';
import { TrackerWorkImportDialog } from './TrackerWorkImportDialog';

const projects: TrackerProject[] = [
  {
    id: 'project-same',
    name: 'Platform',
    description: 'Linear project',
    status: 'started',
    url: 'https://linear.example/platform',
    milestoneCount: 1,
    issueCount: 2,
    slug: 'platform',
    trackerConnectionId: 'linear-main',
    trackerName: 'Linear main',
    trackerType: 'linear',
  },
  {
    id: 'project-same',
    name: 'Platform',
    description: 'GitHub project',
    status: 'open',
    url: 'https://github.example/platform',
    milestoneCount: 0,
    issueCount: 3,
    slug: 'platform',
    trackerConnectionId: 'github-secondary',
    trackerName: 'GitHub secondary',
    trackerType: 'github',
  },
];

const workflow: Workflow = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'Developer Delivery',
  version: '2.0.0',
  nodes: [],
  edges: [],
};

const importedSaga = {
  id: '22222222-2222-4222-8222-222222222222',
  name: 'Platform',
} as Saga;

function trackerService(
  importProject = vi.fn().mockResolvedValue(importedSaga),
): ITrackerBrowserService {
  return {
    listProjects: vi.fn().mockResolvedValue(projects),
    getProject: vi.fn(),
    listMilestones: vi.fn().mockResolvedValue([]),
    listIssues: vi.fn().mockResolvedValue([]),
    importProject,
  };
}

function workflowService(
  listVersions = vi.fn().mockResolvedValue([
    {
      version: '1.5.0',
      documentRevision: 'revision-1',
      createdAt: '2026-09-19T10:00:00Z',
      isHead: false,
    },
    {
      version: '2.0.0',
      documentRevision: 'revision-2',
      createdAt: '2026-09-20T10:00:00Z',
      isHead: true,
    },
  ]),
): IWorkflowService {
  return {
    listWorkflows: vi.fn().mockResolvedValue([workflow]),
    getWorkflow: vi.fn(),
    listWorkflowVersions: listVersions,
    getWorkflowVersion: vi.fn(),
    saveWorkflow: vi.fn(),
    deleteWorkflow: vi.fn(),
    exportWorkflow: vi.fn(),
    previewWorkflowImport: vi.fn(),
    applyWorkflowImport: vi.fn(),
    launchWorkflow: vi.fn(),
  };
}

const dispatchService: IDispatchBus = {
  getQueue: vi.fn().mockResolvedValue([]),
  getClusters: vi.fn().mockResolvedValue([
    {
      instanceId: 'instance-1',
      connectionId: 'forge-local',
      name: 'Local Forge',
      url: 'http://forge.local',
      enabled: true,
    },
  ]),
  approve: vi.fn(),
  dispatch: vi.fn(),
  dispatchBatch: vi.fn(),
};

const repoService = {
  getRepos: vi.fn().mockResolvedValue([
    {
      name: 'volundr',
      owner: 'niuulabs',
      org: 'niuulabs',
      cloneUrl: 'https://github.com/niuulabs/volundr.git',
      defaultBranch: 'main',
      branches: ['main', 'dev'],
    },
  ]),
  getBranches: vi.fn().mockResolvedValue(['main', 'dev']),
};

function renderDialog({
  tracker = trackerService(),
  workflows = workflowService(),
  connectedProjects = [],
  onOpenChange = vi.fn(),
  onImported = vi.fn(),
  onOpenProject = vi.fn(),
}: {
  tracker?: ITrackerBrowserService;
  workflows?: IWorkflowService;
  connectedProjects?: WorkSummary[];
  onOpenChange?: (open: boolean) => void;
  onImported?: (saga: Saga) => void;
  onOpenProject?: (project: WorkSummary) => void;
} = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          'ting.tracker': tracker,
          'ting.workflows': workflows,
          'ting.dispatch': dispatchService,
          'niuu.repos': repoService,
        }}
      >
        <TrackerWorkImportDialog
          open
          onOpenChange={onOpenChange}
          onImported={onImported}
          connectedProjects={connectedProjects}
          onOpenProject={onOpenProject}
        />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { ...result, client };
}

async function selectRepository() {
  await screen.findByText(/Linear main · 2 tickets/);
  fireEvent.change(screen.getByTestId('work-import-repository'), {
    target: { value: 'niuulabs/volundr' },
  });
  await waitFor(() => expect(screen.getByTestId('work-import-branch')).toHaveValue('main'));
}

describe('TrackerWorkImportDialog', () => {
  it('keeps same-named projects distinct by tracker connection', async () => {
    renderDialog();
    expect(await screen.findByText('Linear main · 2 tickets')).toBeInTheDocument();
    expect(screen.getByText('GitHub secondary · 3 tickets')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Platform/ })).toHaveLength(2);
  });

  it('imports assignments without starting execution and returns the new project', async () => {
    const importProject = vi.fn().mockResolvedValue(importedSaga);
    const onImported = vi.fn();
    const onOpenChange = vi.fn();
    renderDialog({ tracker: trackerService(importProject), onImported, onOpenChange });
    await selectRepository();
    fireEvent.change(screen.getByLabelText('Workflow'), { target: { value: workflow.id } });
    await waitFor(() => expect(screen.getByLabelText('Version')).toHaveValue('2.0.0'));
    fireEvent.change(screen.getByLabelText('Execution target'), {
      target: { value: 'instance-1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import project' }));

    await waitFor(() => expect(onImported).toHaveBeenCalledWith(importedSaga));
    expect(importProject).toHaveBeenCalledWith(
      'project-same',
      ['niuulabs/volundr'],
      'main',
      'instance-1',
      {
        repoRefs: [{ repo: 'niuulabs/volundr', branch: 'main' }],
        trackerConnectionId: 'linear-main',
        target: { mode: 'instance', instanceId: 'instance-1' },
        workflowId: workflow.id,
        workflowVersion: '2.0.0',
      },
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(screen.getByTestId('work-import-repository')).toHaveValue('');
  });

  it('retains form state and shows an import error', async () => {
    const importProject = vi.fn().mockRejectedValue(new Error('source permission denied'));
    renderDialog({ tracker: trackerService(importProject) });
    await selectRepository();
    fireEvent.click(screen.getByRole('button', { name: 'Import project' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('source permission denied');
    expect(screen.getByTestId('work-import-repository')).toHaveValue('niuulabs/volundr');
  });

  it('prevents source-qualified reimport and opens the existing setup', async () => {
    const existing = {
      id: 'project:saga-1',
      kind: 'project',
      title: 'Platform',
      source: {
        connectionId: 'linear-main',
        provider: 'Linear',
        projectId: 'project-same',
        url: 'https://linear.example/platform',
        status: 'started',
      },
    } as WorkSummary;
    const onOpenProject = vi.fn();
    renderDialog({ connectedProjects: [existing], onOpenProject });

    expect(await screen.findByText(/already connected/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import project' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Open connected project' }));
    expect(onOpenProject).toHaveBeenCalledWith(existing);
  });

  it('does not confuse the same project id on a different connection', async () => {
    const existing = {
      id: 'project:saga-1',
      kind: 'project',
      title: 'Platform',
      source: {
        connectionId: 'linear-main',
        provider: 'Linear',
        projectId: 'project-same',
        url: 'https://linear.example/platform',
        status: 'started',
      },
    } as WorkSummary;
    renderDialog({ connectedProjects: [existing] });
    const github = await screen.findByRole('button', { name: /GitHub secondary/i });
    fireEvent.click(github);
    expect(screen.queryByText(/already connected/i)).not.toBeInTheDocument();
    await selectRepository();
    expect(screen.getByRole('button', { name: 'Import project' })).toBeEnabled();
  });

  it('cannot be cancelled while an import is pending', async () => {
    let finish: ((saga: Saga) => void) | undefined;
    const importProject = vi.fn().mockImplementation(
      () =>
        new Promise<Saga>((resolve) => {
          finish = resolve;
        }),
    );
    const onOpenChange = vi.fn();
    const onImported = vi.fn();
    renderDialog({ tracker: trackerService(importProject), onOpenChange, onImported });
    await selectRepository();
    fireEvent.click(screen.getByRole('button', { name: 'Import project' }));

    const cancel = screen.getByRole('button', { name: 'Cancel' });
    expect(cancel).toBeDisabled();
    fireEvent.click(cancel);
    expect(onOpenChange).not.toHaveBeenCalled();

    finish?.(importedSaga);
    await waitFor(() => expect(onImported).toHaveBeenCalledWith(importedSaga));
  });

  it('shows target catalog failures instead of silently using default routing', async () => {
    const failingDispatch = {
      ...dispatchService,
      getClusters: vi.fn().mockRejectedValue(new Error('target catalog offline')),
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            'ting.tracker': trackerService(),
            'ting.workflows': workflowService(),
            'ting.dispatch': failingDispatch,
            'niuu.repos': repoService,
          }}
        >
          <TrackerWorkImportDialog
            open
            onOpenChange={vi.fn()}
            onImported={vi.fn()}
            connectedProjects={[]}
            onOpenProject={vi.fn()}
          />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('target catalog offline')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Import project' })).not.toBeEnabled();
  });

  it('blocks import when an explicitly selected workflow version cannot be loaded', async () => {
    renderDialog({
      workflows: workflowService(vi.fn().mockRejectedValue(new Error('version history offline'))),
    });
    await selectRepository();
    fireEvent.change(screen.getByLabelText('Workflow'), { target: { value: workflow.id } });

    expect(await screen.findByRole('alert')).toHaveTextContent(/versions could not be loaded/i);
    expect(screen.getByRole('button', { name: 'Import project' })).toBeDisabled();
  });

  it('does not switch to another tracker project when the selected project disappears', async () => {
    const listProjects = vi
      .fn()
      .mockResolvedValueOnce(projects)
      .mockResolvedValueOnce([projects[1]]);
    const importProject = vi.fn().mockResolvedValue(importedSaga);
    const tracker = { ...trackerService(importProject), listProjects };
    const { client } = renderDialog({ tracker });
    await selectRepository();
    fireEvent.click(screen.getAllByRole('button', { name: /Platform/ })[0]!);

    await act(() =>
      client.invalidateQueries({ queryKey: ['ting', 'tracker', 'projects'], exact: true }),
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /selected tracker project is no longer available/i,
    );
    expect(screen.getByRole('button', { name: 'Import project' })).toBeDisabled();
    expect(importProject).not.toHaveBeenCalled();
  });

  it('pins the initially displayed tracker project across a reordered refresh', async () => {
    const listProjects = vi
      .fn()
      .mockResolvedValueOnce(projects)
      .mockResolvedValueOnce([projects[1], projects[0]]);
    const importProject = vi.fn().mockResolvedValue(importedSaga);
    const tracker = { ...trackerService(importProject), listProjects };
    const { client } = renderDialog({ tracker });
    await selectRepository();

    await act(() =>
      client.invalidateQueries({ queryKey: ['ting', 'tracker', 'projects'], exact: true }),
    );

    expect(screen.getByText('Linear main · 2 tickets').closest('button')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Import project' }));
    await waitFor(() => expect(importProject).toHaveBeenCalled());
    expect(importProject.mock.calls[0]?.[4]).toEqual(
      expect.objectContaining({ trackerConnectionId: 'linear-main' }),
    );
  });

  it('does not retain an unavailable workflow after the catalog refreshes', async () => {
    const listWorkflows = vi.fn().mockResolvedValueOnce([workflow]).mockResolvedValueOnce([]);
    const workflows = { ...workflowService(), listWorkflows };
    const { client } = renderDialog({ workflows });
    await selectRepository();
    fireEvent.change(screen.getByLabelText('Workflow'), { target: { value: workflow.id } });
    await waitFor(() => expect(screen.getByLabelText('Version')).toHaveValue('2.0.0'));

    await act(() => client.invalidateQueries({ queryKey: ['ting', 'workflows'], exact: true }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /selected workflow is no longer available/i,
    );
    expect(screen.getByRole('button', { name: 'Import project' })).toBeDisabled();
  });

  it('does not replace a selected historical version with the new workflow head', async () => {
    const listVersions = vi
      .fn()
      .mockResolvedValueOnce([
        {
          version: '1.5.0',
          documentRevision: 'revision-1',
          createdAt: '2026-09-19T10:00:00Z',
          isHead: false,
        },
        {
          version: '2.0.0',
          documentRevision: 'revision-2',
          createdAt: '2026-09-20T10:00:00Z',
          isHead: true,
        },
      ])
      .mockResolvedValueOnce([
        {
          version: '2.0.0',
          documentRevision: 'revision-2',
          createdAt: '2026-09-20T10:00:00Z',
          isHead: true,
        },
      ]);
    const { client } = renderDialog({ workflows: workflowService(listVersions) });
    await selectRepository();
    fireEvent.change(screen.getByLabelText('Workflow'), { target: { value: workflow.id } });
    await waitFor(() => expect(screen.getByLabelText('Version')).toHaveValue('2.0.0'));
    await screen.findByRole('option', { name: '1.5.0' });
    fireEvent.change(screen.getByLabelText('Version'), { target: { value: '1.5.0' } });

    await act(() =>
      client.invalidateQueries({
        queryKey: ['ting', 'workflows', workflow.id, 'versions'],
        exact: true,
      }),
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /selected workflow version is no longer available/i,
    );
    expect(screen.getByRole('button', { name: 'Import project' })).toBeDisabled();
    expect(screen.getByLabelText('Version')).toHaveValue('1.5.0');
    expect(screen.getByRole('option', { name: '1.5.0 · unavailable' })).toBeInTheDocument();
  });

  it('marks an explicitly selected historical version unavailable when history refreshes empty', async () => {
    const listVersions = vi
      .fn()
      .mockResolvedValueOnce([
        {
          version: '1.5.0',
          documentRevision: 'revision-1',
          createdAt: '2026-09-19T10:00:00Z',
          isHead: false,
        },
        {
          version: '2.0.0',
          documentRevision: 'revision-2',
          createdAt: '2026-09-20T10:00:00Z',
          isHead: true,
        },
      ])
      .mockResolvedValueOnce([]);
    const { client } = renderDialog({ workflows: workflowService(listVersions) });
    await selectRepository();
    fireEvent.change(screen.getByLabelText('Workflow'), { target: { value: workflow.id } });
    await screen.findByRole('option', { name: '1.5.0' });
    fireEvent.change(screen.getByLabelText('Version'), { target: { value: '1.5.0' } });

    await act(() =>
      client.invalidateQueries({
        queryKey: ['ting', 'workflows', workflow.id, 'versions'],
        exact: true,
      }),
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /selected workflow version is no longer available/i,
    );
    expect(screen.getByLabelText('Version')).toHaveValue('1.5.0');
    expect(screen.getByRole('button', { name: 'Import project' })).toBeDisabled();
  });
});
