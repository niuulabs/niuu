import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { RunWorkflowGraph } from './RunWorkflowGraph';
import type { RunGraph } from '../domain/runGraph';
import { runGraphIsMoving } from '../domain/runGraph';
import { placeWorkflowGraph } from '../domain/workflowCanvas';

function runGraph(): RunGraph {
  const { nodes, edges } = placeWorkflowGraph(
    [
      { id: 'go', kind: 'trigger', label: 'Requested', dispatchEvent: 'code.requested' },
      {
        id: 'code',
        kind: 'stage',
        label: 'Implement',
        stageMembers: [{ personaId: 'coder' }],
      },
      { id: 'approve', kind: 'gate', label: 'Approve merge', mode: 'human_approval' },
    ] as never,
    [
      { id: 'e1', source: 'go', target: 'code', label: 'code.requested' },
      { id: 'e2', source: 'code', target: 'approve', label: 'code.changed' },
    ] as never,
  );
  return {
    workflowName: 'Code, Review and Merge',
    nodes,
    edges,
    states: new Map([
      ['go', { nodeId: 'go', state: 'done', members: [] }],
      [
        'code',
        {
          nodeId: 'code',
          state: 'done',
          members: [
            { member: 'coder', state: 'done', verdict: 'pass', sessionId: 'session-coder' },
          ],
        },
      ],
      ['approve', { nodeId: 'approve', state: 'waiting', members: [] }],
    ]),
  };
}

const WAITING_REVIEWS = {
  run: { slug: 'run-1' },
  unreadableRuns: [],
  reviews: [
    {
      columnId: 'approve',
      title: 'Approve merge',
      review: null,
      workflowName: 'Code, Review and Merge',
      taskId: 'run-1',
      sessionId: '',
      panels: [],
      revisions: [
        {
          number: 1,
          gateId: 'approve',
          activationId: 'activation-1',
          requestedAt: '2026-11-09T12:00:00Z',
          status: 'pending',
          summary: 'PR #22 passed review and the security audit.',
          decidedBy: '',
          notes: '',
          decidedAt: '',
          fields: {},
        },
      ],
    },
  ],
};

function renderGraph(
  runGraphFn: () => Promise<RunGraph>,
  onOpenSession = vi.fn(),
  decideRunGate = vi.fn(async () => {}),
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const work = { runGraph: runGraphFn, runReviews: async () => WAITING_REVIEWS, decideRunGate };
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ 'ting.work': work }}>
        <RunWorkflowGraph runId="run-1" onOpenSession={onOpenSession} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return onOpenSession;
}

describe('RunWorkflowGraph', () => {
  it('draws the run on its workflow, each node with where the run is at it', async () => {
    renderGraph(async () => runGraph());

    expect(await screen.findByTestId('workflow-run-state-code')).toHaveAttribute(
      'data-run-state',
      'done',
    );
    expect(screen.getByTestId('workflow-run-state-approve')).toHaveTextContent('Needs you');
    expect(screen.getByTestId('workflow-run-state-go')).toHaveTextContent('Done');
    expect(screen.getByTestId('workflow-run-member-code-coder')).toHaveTextContent('coder · pass');
  });

  it('frames the run on its canvas once it has a size, as a viewer that only shows it', async () => {
    const box = { x: 0, y: 0, top: 0, left: 0, right: 800, bottom: 300, width: 800, height: 300 };
    const sized = vi
      .spyOn(SVGElement.prototype, 'getBoundingClientRect')
      .mockReturnValue({ ...box, toJSON: () => box } as DOMRect);
    try {
      renderGraph(async () => runGraph());

      const canvas = await screen.findByTestId('run-graph');
      const frame = canvas.querySelector('svg g[transform]');
      expect(frame?.getAttribute('transform')).not.toBe('translate(0,0) scale(1)');
    } finally {
      sized.mockRestore();
    }
  });

  it('centres what is drawn in its canvas', async () => {
    const box = { x: 0, y: 0, top: 0, left: 0, right: 800, bottom: 300, width: 800, height: 300 };
    const sized = vi
      .spyOn(SVGElement.prototype, 'getBoundingClientRect')
      .mockReturnValue({ ...box, toJSON: () => box } as DOMRect);
    const drawn = Object.assign(() => ({ x: 100, y: 40, width: 400, height: 100 }) as DOMRect, {});
    const original = (SVGElement.prototype as { getBBox?: unknown }).getBBox;
    (SVGElement.prototype as { getBBox?: unknown }).getBBox = drawn;
    try {
      renderGraph(async () => runGraph());

      const canvas = await screen.findByTestId('run-graph');
      // 400x100 drawn at (100,40) fits at 1:1 and sits in the middle of 800x300.
      expect(canvas.querySelector('svg g[transform]')?.getAttribute('transform')).toBe(
        'translate(100,60) scale(1)',
      );
    } finally {
      sized.mockRestore();
      (SVGElement.prototype as { getBBox?: unknown }).getBBox = original;
    }
  });

  it('marks the way the run went, from a step that closed to one it reached', async () => {
    renderGraph(async () => runGraph());

    await screen.findByTestId('workflow-run-state-code');
    expect(screen.getByTestId('workflow-edge-e1')).toHaveAttribute('data-run-passed', 'true');
    expect(screen.getByTestId('workflow-edge-e2')).toHaveAttribute('data-run-passed', 'true');
  });

  it('lets a step the run has not reached recede, with no label of its own', async () => {
    const graph = runGraph();
    graph.states.set('approve', { nodeId: 'approve', state: 'pending', members: [] });
    renderGraph(async () => graph);

    const approve = await screen.findByTestId('workflow-run-state-approve');
    expect(approve).toHaveAttribute('data-run-state', 'pending');
    expect(approve).not.toHaveTextContent('Not reached');
  });

  it('answers a waiting gate where the run says Needs you', async () => {
    const decideRunGate = vi.fn(async () => {});
    renderGraph(async () => runGraph(), vi.fn(), decideRunGate);

    const gate = await screen.findByTestId('run-gate');
    expect(gate).toHaveTextContent('Approve merge');
    expect(gate).toHaveTextContent('PR #22 passed review and the security audit.');
    expect(within(gate).getByRole('link', { name: 'Open the review' })).toHaveAttribute(
      'href',
      '/ting/review/run/run-1',
    );

    fireEvent.click(within(gate).getByRole('button', { name: /^Approve/ }));

    await waitFor(() =>
      expect(decideRunGate).toHaveBeenCalledWith('run-1', {
        gateId: 'approve',
        activationId: 'activation-1',
        decision: 'approve',
        notes: '',
      }),
    );
  });

  it('shows no decision while no gate waits', async () => {
    const graph = runGraph();
    graph.states.set('approve', { nodeId: 'approve', state: 'done', members: [] });
    renderGraph(async () => graph);

    await screen.findByTestId('workflow-run-state-approve');
    expect(screen.queryByTestId('run-gate')).toBeNull();
  });

  it("opens a member's session from its chip", async () => {
    const onOpenSession = renderGraph(async () => runGraph());

    fireEvent.click(await screen.findByRole('button', { name: "Open coder's session" }));

    expect(onOpenSession).toHaveBeenCalledWith('session-coder');
  });

  it('says it is loading, then says why it could not read the run', async () => {
    renderGraph(() => Promise.reject(new Error('Run not found: run-1')));

    expect(screen.getByTestId('run-graph-loading')).toBeInTheDocument();
    expect(await screen.findByTestId('run-graph-error')).toHaveTextContent('Run not found: run-1');
  });

  it('follows a run while something at a node can still change', () => {
    const graph = runGraph();
    expect(runGraphIsMoving(graph)).toBe(true);
    graph.states.set('approve', { nodeId: 'approve', state: 'done', members: [] });
    expect(runGraphIsMoving(graph)).toBe(false);
  });
});
