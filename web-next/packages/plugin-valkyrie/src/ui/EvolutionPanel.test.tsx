import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { EvolutionFold, EvolutionPanel } from './EvolutionPanel';
import type { EvolutionChange, EvolutionReport, ValkyrieDashboard } from '../domain';

const CHANGE: EvolutionChange = {
  kind: 'built',
  tool: 'node_inspect_resolve',
  at: '2026-10-30T09:00:00Z',
  runs: 3,
  successes: 3,
  peer: '',
  version: 1,
  reason: '',
  replacedBy: '',
};

const REPORT: EvolutionReport = {
  headline: 'Simpler now: 2 duplicate tools retired.',
  windowSeconds: 604800,
  tools: 19,
  retired: 31,
  fromPeers: 10,
  runs: 34912,
  successes: 34912,
  mostUsed: [
    {
      tool: 'k8s_pod_logs_since_sa',
      runs: 27310,
      successes: 27303,
      lastUsedAt: '2026-10-31T10:00:00Z',
    },
    { tool: 'half_broken', runs: 10, successes: 5, lastUsedAt: '' },
  ],
  changes: [
    {
      ...CHANGE,
      kind: 'retired',
      tool: 'prometheus_readonly_query',
      reason: 'superseded',
      replacedBy: 'prometheus_range_query_absolute',
    },
    { ...CHANGE, kind: 'retired', tool: 'old_probe', reason: 'unused' },
    { ...CHANGE, kind: 'picked_up', tool: 'discover_and_inspect_pod', peer: 'glitnir-id' },
    { ...CHANGE, kind: 'picked_up', tool: 'from_stranger', peer: 'unknown-id' },
    { ...CHANGE, kind: 'repaired', tool: 'prometheus_range_query_absolute', version: 2 },
    CHANGE,
  ],
  attention: [],
};

const DASHBOARD = {
  valkyries: [{ id: 'glitnir-id', name: 'Hildr' }],
} as unknown as ValkyrieDashboard;

function renderPanel(report: EvolutionReport | null) {
  const onViewSkill = vi.fn();
  render(<EvolutionPanel dashboard={DASHBOARD} report={report} onViewSkill={onViewSkill} />);
  return onViewSkill;
}

describe('EvolutionPanel', () => {
  it('leads with the headline', () => {
    renderPanel(REPORT);

    expect(screen.getByTestId('evolution-headline')).toHaveTextContent(REPORT.headline);
  });

  it('shows the most used tools with runs, success and last use', () => {
    const onViewSkill = renderPanel(REPORT);

    const busy = screen.getByTestId('evolution-tool-k8s_pod_logs_since_sa');
    expect(busy).toHaveTextContent('27,310');
    expect(busy).toHaveTextContent('100%');
    expect(busy).toHaveTextContent('ago');
    const halfBroken = screen.getByTestId('evolution-tool-half_broken');
    expect(halfBroken).toHaveTextContent('50%');
    expect(halfBroken).toHaveTextContent('—');

    fireEvent.click(screen.getByTestId('skill-link-half_broken'));

    expect(onViewSkill).toHaveBeenCalledWith('half_broken');
  });

  it('shows at most five tools', () => {
    const mostUsed = Array.from({ length: 7 }, (_, index) => ({
      tool: `tool_${index}`,
      runs: 7 - index,
      successes: 7 - index,
      lastUsedAt: '',
    }));
    renderPanel({ ...REPORT, mostUsed });

    expect(screen.getByTestId('evolution-tool-tool_4')).toBeInTheDocument();
    expect(screen.queryByTestId('evolution-tool-tool_5')).not.toBeInTheDocument();
  });

  it('leaves the table out before any tool has run', () => {
    renderPanel({ ...REPORT, mostUsed: [] });

    expect(screen.queryByTestId('evolution-most-used')).not.toBeInTheDocument();
  });

  it('says what changed in plain words', () => {
    renderPanel(REPORT);

    expect(screen.getByTestId('evolution-change-prometheus_readonly_query')).toHaveTextContent(
      'same as prometheus_range_query_absolute',
    );
    expect(screen.getByTestId('evolution-change-old_probe')).toHaveTextContent('Never used');
    expect(screen.getByTestId('evolution-change-discover_and_inspect_pod')).toHaveTextContent(
      'from Hildr',
    );
    expect(screen.getByTestId('evolution-change-from_stranger')).toHaveTextContent(
      'from unknown-id',
    );
    expect(
      screen.getByTestId('evolution-change-prometheus_range_query_absolute'),
    ).toHaveTextContent('Repaired');
  });

  it('folds older changes away', () => {
    renderPanel(REPORT);

    expect(screen.getByTestId('evolution-fold-changes')).toHaveTextContent('1 older changes');
    expect(
      screen.getByTestId('evolution-change-node_inspect_resolve').closest('details'),
    ).not.toHaveAttribute('open');
  });

  it('skips the list when nothing changed', () => {
    renderPanel({ ...REPORT, changes: [] });

    expect(screen.queryByText('What changed')).not.toBeInTheDocument();
  });

  it('waits for the first dream', () => {
    renderPanel(null);

    expect(screen.getByTestId('evolution-empty')).toHaveTextContent('next dream');
  });

  it('keeps a fold closed until asked', () => {
    render(
      <EvolutionFold id="closed" label="Closed">
        <p>hidden</p>
      </EvolutionFold>,
    );

    expect(screen.getByTestId('evolution-fold-closed').closest('details')).not.toHaveAttribute(
      'open',
    );
  });
});
