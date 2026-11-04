import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { WorkBoardColumn, WorkDetail } from '../domain/work';
import type { Workflow } from '../domain/workflow';
import { TrackerBoard } from './TrackerBoard';

vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => '/here',
  Link: ({ children, to, role }: { children: ReactNode; to: string; role?: string }) => (
    <a href={to} role={role}>
      {children}
    </a>
  ),
}));

function column(
  id: string,
  title: string,
  workflow: [string, string] | null,
  kind: WorkBoardColumn['kind'] = 'work',
): WorkBoardColumn {
  return {
    id,
    title,
    kind: workflow ? kind : 'tracker',
    review: null,
    approveColumnId: null,
    changesColumnId: null,
    workflowId: workflow?.[0] ?? null,
    workflowName: workflow?.[1] ?? null,
  };
}

const PLAN: [string, string] = ['wf-plan', 'Ticket Planning Flow'];
const DELIVER: [string, string] = ['wf-deliver', 'Reviewed Delivery Flow'];

const BOARD: WorkDetail['board'] = {
  columns: [
    column('backlog', 'Backlog', null),
    column('planning', 'Planning', PLAN),
    column('plan-review', 'Plan review', PLAN, 'gate'),
    column('building', 'Building', DELIVER),
    column('done', 'Done', null),
  ],
};

function workflow(id: string, name: string, extra: Record<string, unknown> = {}): Workflow {
  return {
    id,
    name,
    scope: 'system',
    graph: {
      board: { columns: [{ title: 'Working' }, { title: 'Review', gate: 'review-gate' }] },
      ...extra,
    },
  } as unknown as Workflow;
}

const CATALOG = [
  workflow('wf-plan', 'Ticket Planning Flow'),
  workflow('wf-deliver', 'Reviewed Delivery Flow'),
  workflow('wf-work', 'Ticket Work Flow', { requiresRepository: false }),
  workflow('wf-code', 'Code Only Flow'),
  { ...workflow('wf-mine', 'My Own Flow'), scope: 'user' } as Workflow,
  { id: 'wf-plain', name: 'Research Campaign', scope: 'system', graph: {} } as unknown as Workflow,
];

function setup({
  board = BOARD,
  repos = [] as string[],
  workflowId = undefined as string | undefined,
} = {}) {
  const saga = { id: 'saga-1', trackerId: 'tracker-1', name: 'Copy', repos, workflowId };
  const ting = {
    getSagas: async () => [saga],
    setBoardWorkflows: vi.fn(async (_id: string, ids: string[]) =>
      ids.map((id) => ({ id, name: id })),
    ),
    assignWorkflow: vi.fn(async () => ({ ...saga, workflowId: undefined })),
  };
  const services = {
    ting,
    'ting.work': { get: async () => ({ board, tasks: [], coverage: [] }) },
    'ting.workflows': { listWorkflows: async () => CATALOG },
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={services}>
        <TrackerBoard trackerId="tracker-1" fallback="not connected" />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return ting;
}

async function openMenu(name: string) {
  fireEvent.click(await screen.findByRole('button', { name: `${name} options` }));
  return screen.getByRole('menu', { name: `${name} options` });
}

describe('board workflows edited on the board', () => {
  it('runs a later workflow earlier, saving the new order at once', async () => {
    const ting = setup();
    const menu = await openMenu('Reviewed Delivery Flow');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Run it earlier' }));
    await waitFor(() =>
      expect(ting.setBoardWorkflows).toHaveBeenCalledWith('saga-1', ['wf-deliver', 'wf-plan']),
    );
  });

  it('cannot move the first workflow earlier, and links each one to the builder', async () => {
    setup();
    const menu = await openMenu('Ticket Planning Flow');
    expect(within(menu).queryByRole('menuitem', { name: 'Run it earlier' })).toBeNull();
    expect(within(menu).getByRole('menuitem', { name: 'Open in the builder' })).toHaveAttribute(
      'href',
      '/ting/workflows/build',
    );
  });

  it('takes a workflow off the board', async () => {
    const ting = setup();
    const menu = await openMenu('Ticket Planning Flow');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Take off the board' }));
    await waitFor(() =>
      expect(ting.setBoardWorkflows).toHaveBeenCalledWith('saga-1', ['wf-deliver']),
    );
    expect(ting.assignWorkflow).not.toHaveBeenCalled();
  });

  it('clears the old project workflow when the last board workflow is taken off', async () => {
    const ting = setup({
      board: {
        columns: [column('backlog', 'Backlog', null), column('planning', 'Planning', PLAN)],
      },
      workflowId: 'wf-plan',
    });
    const menu = await openMenu('Ticket Planning Flow');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Take off the board' }));
    await waitFor(() => expect(ting.setBoardWorkflows).toHaveBeenCalledWith('saga-1', []));
    await waitFor(() => expect(ting.assignWorkflow).toHaveBeenCalledWith('saga-1', null));
  });

  it('adds only system workflows with columns, and says which need a repository', async () => {
    const ting = setup();
    fireEvent.click(await screen.findByTestId('board-add-workflow'));
    const panel = await screen.findByRole('complementary', { name: 'Add a workflow' });
    await within(panel).findByText('Ticket Work Flow');

    expect(within(panel).queryByText('My Own Flow')).toBeNull();
    expect(within(panel).queryByText('Research Campaign')).toBeNull();
    const planning = within(panel).getByText('Ticket Planning Flow').closest('li')!;
    expect(within(planning).getByText('On this board')).toBeInTheDocument();
    const code = within(panel).getByText('Code Only Flow').closest('li')!;
    expect(within(code).getByText(/needs a repository on this project/)).toBeInTheDocument();
    expect(within(code).queryByRole('button', { name: 'Add' })).toBeNull();
    const work = within(panel).getByText('Ticket Work Flow').closest('li')!;
    expect(within(work).getByText('Review')).toHaveClass('ting-board-chip--gate');

    fireEvent.click(within(work).getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(ting.setBoardWorkflows).toHaveBeenCalledWith('saga-1', [
        'wf-plan',
        'wf-deliver',
        'wf-work',
      ]),
    );
  });

  it('lets a code workflow be added once the project has a repository', async () => {
    const ting = setup({ repos: ['niuulabs/niuu'] });
    fireEvent.click(await screen.findByTestId('board-add-workflow'));
    const panel = await screen.findByRole('complementary', { name: 'Add a workflow' });
    const code = (await within(panel).findByText('Code Only Flow')).closest('li')!;
    fireEvent.click(within(code).getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(ting.setBoardWorkflows).toHaveBeenCalledWith('saga-1', [
        'wf-plan',
        'wf-deliver',
        'wf-code',
      ]),
    );
  });

  it('shows why a change was refused', async () => {
    const ting = setup();
    ting.setBoardWorkflows.mockRejectedValueOnce(new Error('Ticket Work Flow has no board'));
    const menu = await openMenu('Ticket Planning Flow');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Take off the board' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Ticket Work Flow has no board');
  });

  it('starts a board with no workflows from its Backlog and the add column', async () => {
    const ting = setup({ board: null });
    expect(await screen.findByText(/No workflows on this board yet/)).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Backlog' })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('board-add-workflow'));
    const panel = await screen.findByRole('complementary', { name: 'Add a workflow' });
    const work = (await within(panel).findByText('Ticket Work Flow')).closest('li')!;
    fireEvent.click(within(work).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(ting.setBoardWorkflows).toHaveBeenCalledWith('saga-1', ['wf-work']));
    fireEvent.click(screen.getByTestId('board-add-workflow'));
    fireEvent.click(
      within(await screen.findByRole('complementary', { name: 'Add a workflow' })).getByRole(
        'button',
        { name: 'Hide' },
      ),
    );
    expect(screen.queryByRole('complementary', { name: 'Add a workflow' })).toBeNull();
  });
});
