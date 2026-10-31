import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { EvolutionFold, EvolutionPanel } from './EvolutionPanel';
import type { DreamOutcome, EvolutionReport, ValkyrieDashboard, ValkyrieResident } from '../domain';

const REPORT: EvolutionReport = {
  headline: 'Simpler now: 2 duplicate tools retired.',
  windowSeconds: 604800,
  tools: 19,
  retired: 31,
  fromPeers: 10,
  runs: 34912,
  successes: 34912,
  changes: [
    {
      kind: 'retired',
      tool: 'prometheus_readonly_query',
      at: '2026-10-31T14:09:00Z',
      runs: 88,
      successes: 78,
      peer: '',
      version: 0,
      reason: 'superseded',
      replacedBy: 'prometheus_range_query_absolute',
    },
    {
      kind: 'retired',
      tool: 'old_probe',
      at: '2026-10-31T14:09:00Z',
      runs: 0,
      successes: 0,
      peer: '',
      version: 0,
      reason: 'unused',
      replacedBy: '',
    },
    {
      kind: 'picked_up',
      tool: 'discover_and_inspect_pod',
      at: '2026-10-31T10:00:00Z',
      runs: 0,
      successes: 0,
      peer: 'glitnir-id',
      version: 0,
      reason: '',
      replacedBy: '',
    },
    {
      kind: 'repaired',
      tool: 'prometheus_range_query_absolute',
      at: '2026-10-30T10:00:00Z',
      runs: 5,
      successes: 5,
      peer: '',
      version: 2,
      reason: '',
      replacedBy: '',
    },
    {
      kind: 'built',
      tool: 'node_inspect_resolve',
      at: '2026-10-30T09:00:00Z',
      runs: 3,
      successes: 3,
      peer: 'unknown-id',
      version: 1,
      reason: '',
      replacedBy: '',
    },
  ],
  attention: [],
};

function resident(id: string, name: string, dream?: Partial<DreamOutcome>): ValkyrieResident {
  return {
    id,
    name,
    flockId: 'flock',
    lastDream: dream
      ? ({
          status: 'completed',
          finishedAt: '2026-10-31T14:09:00Z',
          error: '',
          ...dream,
        } as DreamOutcome)
      : undefined,
  } as unknown as ValkyrieResident;
}

function dashboard(valkyries: ValkyrieResident[]): ValkyrieDashboard {
  return {
    valkyries,
    flocks: [{ id: 'flock', valkyrieIds: valkyries.map((entry) => entry.id) }],
  } as unknown as ValkyrieDashboard;
}

function renderPanel(valkyrie: ValkyrieResident, others: ValkyrieResident[] = []) {
  const handlers = { onViewSkill: vi.fn(), onSelectResident: vi.fn() };
  render(
    <EvolutionPanel dashboard={dashboard([valkyrie, ...others])} valkyrie={valkyrie} {...handlers}>
      <EvolutionFold id="tools" label="All tools">
        <p>tool list</p>
      </EvolutionFold>
    </EvolutionPanel>,
  );
  return handlers;
}

describe('EvolutionPanel', () => {
  it('leads with the headline and what the resident keeps', () => {
    renderPanel(resident('valhalla', 'Valhalla', { evolution: REPORT }));

    expect(screen.getByTestId('evolution-headline')).toHaveTextContent(
      'Simpler now: 2 duplicate tools retired.',
    );
    expect(screen.getByText('31 retired so far')).toBeInTheDocument();
    expect(screen.getByText('100%')).toBeInTheDocument();
    expect(screen.getByText('of 34,912 runs')).toBeInTheDocument();
    expect(screen.getByText('of its 19 tools')).toBeInTheDocument();
  });

  it('says what changed in plain words', () => {
    const glitnir = resident('glitnir-id', 'Glitnir', { evolution: REPORT });
    renderPanel(resident('valhalla', 'Valhalla', { evolution: REPORT }), [glitnir]);

    expect(screen.getByTestId('evolution-change-prometheus_readonly_query')).toHaveTextContent(
      'Replaced by prometheus_range_query_absolute.',
    );
    expect(screen.getByTestId('evolution-change-discover_and_inspect_pod')).toHaveTextContent(
      'from Glitnir',
    );
    expect(screen.getByTestId('evolution-change-discover_and_inspect_pod')).toHaveTextContent(
      'Not used yet.',
    );
    expect(screen.getByTestId('evolution-change-node_inspect_resolve')).toHaveTextContent(
      '3 of 3 runs worked.',
    );
    expect(screen.getByTestId('evolution-change-old_probe')).toHaveTextContent('Never used');
  });

  it('keeps a long week short', () => {
    const changes = Array.from({ length: 8 }, (_, index) => ({
      ...REPORT.changes[0],
      tool: `tool_${index}`,
    }));
    renderPanel(resident('valhalla', 'Valhalla', { evolution: { ...REPORT, changes } }));

    expect(screen.getByTestId('evolution-fold-changes')).toHaveTextContent('3 older changes');
    expect(screen.getByTestId('evolution-change-tool_7').closest('details')).not.toHaveAttribute(
      'open',
    );
    expect(screen.getByTestId('evolution-change-tool_4').closest('details')).toBeNull();
  });

  it('opens a tool that is still installed', () => {
    const { onViewSkill } = renderPanel(resident('valhalla', 'Valhalla', { evolution: REPORT }));

    fireEvent.click(screen.getByTestId('skill-link-node_inspect_resolve'));

    expect(onViewSkill).toHaveBeenCalledWith('node_inspect_resolve');
    expect(screen.queryByTestId('skill-link-prometheus_readonly_query')).not.toBeInTheDocument();
  });

  it('calls out a tool that never worked', () => {
    const failing: EvolutionReport = {
      ...REPORT,
      attention: [{ tool: 'list_pods_for_job', runs: 33, lastUsedAt: '2026-09-21T00:00:00Z' }],
    };
    renderPanel(resident('valaskjalf', 'Valaskjalf', { evolution: failing }));

    expect(screen.getByTestId('evolution-attention')).toHaveTextContent(
      'list_pods_for_job failed all 33 runs and is still installed.',
    );
  });

  it('shows no percentage before anything has run', () => {
    renderPanel(resident('new', 'New', { evolution: { ...REPORT, runs: 0, successes: 0 } }));

    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('compares the flock, busiest first, and flags a failing resident', () => {
    const quiet: EvolutionReport = {
      ...REPORT,
      runs: 97,
      successes: 63,
      attention: [{ tool: 'broken', runs: 33, lastUsedAt: '' }],
    };
    const { onSelectResident } = renderPanel(
      resident('valhalla', 'Valhalla', { evolution: REPORT }),
      [resident('valaskjalf', 'Valaskjalf', { evolution: quiet }), resident('fresh', 'Fresh')],
    );

    const table = screen.getByTestId('evolution-flock');
    const rows = within(table).getAllByRole('button');
    expect(rows.map((row) => row.getAttribute('data-testid'))).toEqual([
      'evolution-flock-valhalla',
      'evolution-flock-valaskjalf',
    ]);
    expect(within(rows[1]).getByLabelText('Has a failing tool')).toBeInTheDocument();
    expect(rows[1]).toHaveTextContent('65%');

    fireEvent.click(rows[1]);

    expect(onSelectResident).toHaveBeenCalledWith('valaskjalf');
  });

  it('leaves the flock out for a resident on its own', () => {
    renderPanel(resident('valhalla', 'Valhalla', { evolution: REPORT }));

    expect(screen.queryByTestId('evolution-flock')).not.toBeInTheDocument();
  });

  it('keeps a failed dream loud', () => {
    renderPanel(
      resident('valhalla', 'Valhalla', { status: 'failed', error: 'TimeoutError: slow' }),
    );

    expect(screen.getByRole('alert')).toHaveTextContent('TimeoutError: slow');
    expect(screen.getByTestId('evolution-empty')).toBeInTheDocument();
  });

  it('says so when a failed dream gave no reason', () => {
    renderPanel(resident('valhalla', 'Valhalla', { status: 'failed' }));

    expect(screen.getByRole('alert')).toHaveTextContent('without reporting an error');
  });

  it('waits for the first dream', () => {
    renderPanel(resident('valhalla', 'Valhalla'));

    expect(screen.getByTestId('evolution-empty')).toHaveTextContent('next dream');
  });

  it('folds the detail away unless told otherwise', () => {
    render(
      <>
        <EvolutionFold id="closed" label="Closed">
          <p>hidden</p>
        </EvolutionFold>
        <EvolutionFold id="open" label="Open" defaultOpen>
          <p>shown</p>
        </EvolutionFold>
      </>,
    );

    expect(screen.getByTestId('evolution-fold-closed').closest('details')).not.toHaveAttribute(
      'open',
    );
    expect(screen.getByTestId('evolution-fold-open').closest('details')).toHaveAttribute('open');
  });
});
