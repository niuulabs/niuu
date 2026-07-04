import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createSeedValkyrieDashboard } from '../adapters/mock';
import type { ValkyrieDashboard, ValkyrieEventTelemetry } from '../domain';
import { Roster } from './Roster';

function renderRoster(dashboard: ValkyrieDashboard = createSeedValkyrieDashboard()) {
  const onSelect = vi.fn();
  render(<Roster dashboard={dashboard} selectedId="valkyrie-valhalla-sigrun" onSelect={onSelect} />);
  return { onSelect, dashboard };
}

describe('Roster', () => {
  it('groups by environment type by default, with counts and health per row', () => {
    renderRoster();

    const k8s = screen.getByTestId('roster-group-kubernetes');
    expect(k8s).toHaveTextContent('Kubernetes');
    expect(within(k8s).getByRole('button', { name: /kubernetes/i })).toHaveTextContent('2');
    expect(screen.getByTestId('roster-group-host')).toHaveTextContent('Inbox / Host');
    expect(screen.getByTestId('roster-group-printer')).toHaveTextContent('Printer / Pi Cell');

    // Row: name, wakefulness, and the ENVIRONMENT health (watch/healthy/degraded).
    const sigrun = screen.getByTestId('roster-item-valkyrie-valhalla-sigrun');
    expect(sigrun).toHaveTextContent('Sigrun');
    expect(sigrun).toHaveTextContent('wakeful');
    expect(sigrun).toHaveTextContent('watch');
    expect(sigrun).toHaveAttribute('aria-pressed', 'true');
    const eir = screen.getByTestId('roster-item-valkyrie-printer-eir');
    expect(eir).toHaveTextContent('degraded');
  });

  it('regroups by flock and by environment via the group-mode switcher', async () => {
    const user = userEvent.setup();
    renderRoster();

    await user.selectOptions(screen.getByLabelText('Group roster by'), ['flock']);
    expect(screen.getByTestId('roster-group-flock-k8s')).toHaveTextContent('Kubernetes Valkyries');
    expect(screen.getByTestId('roster-group-flock-personal')).toHaveTextContent(
      'Personal Sentinels',
    );
    expect(screen.getByTestId('roster-group-flock-printers')).toHaveTextContent(
      'Printer Operators',
    );

    await user.selectOptions(screen.getByLabelText('Group roster by'), ['environment']);
    expect(screen.getByTestId('roster-group-env-k8s-valhalla')).toHaveTextContent('Valhalla k8s');
    expect(screen.getByTestId('roster-group-env-host-jozef')).toHaveTextContent('Jozef host');
  });

  it('filters residents and shows an empty state when nothing matches', async () => {
    const user = userEvent.setup();
    renderRoster();

    await user.type(screen.getByLabelText('Filter valkyries'), 'saga');
    expect(screen.getByTestId('roster-item-valkyrie-host-email')).toBeInTheDocument();
    expect(screen.queryByTestId('roster-item-valkyrie-valhalla-sigrun')).not.toBeInTheDocument();
    expect(screen.queryByTestId('roster-group-kubernetes')).not.toBeInTheDocument();

    await user.clear(screen.getByLabelText('Filter valkyries'));
    await user.type(screen.getByLabelText('Filter valkyries'), 'no-such-valkyrie');
    expect(screen.getByTestId('roster-empty')).toHaveTextContent('No valkyries match');
  });

  it('collapses and expands a group', async () => {
    const user = userEvent.setup();
    renderRoster();

    const header = within(screen.getByTestId('roster-group-kubernetes')).getByRole('button', {
      name: /kubernetes/i,
    });
    await user.click(header);
    expect(header).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('roster-item-valkyrie-valhalla-sigrun')).not.toBeInTheDocument();
    // Other groups stay open.
    expect(screen.getByTestId('roster-item-valkyrie-host-email')).toBeInTheDocument();

    await user.click(header);
    expect(screen.getByTestId('roster-item-valkyrie-valhalla-sigrun')).toBeInTheDocument();
  });

  it('selects a resident on click', async () => {
    const user = userEvent.setup();
    const { onSelect } = renderRoster();

    await user.click(screen.getByRole('button', { name: /Runa/ }));
    expect(onSelect).toHaveBeenCalledWith('valkyrie-valhalla-runa');
  });

  it('lights activity bars from recent telemetry credited to the resident', () => {
    const dashboard = createSeedValkyrieDashboard();
    const recent: ValkyrieEventTelemetry = {
      id: 'evt-now',
      eventType: 'ravn.valkyrie.judgment.recorded',
      kind: 'judgment',
      environmentId: 'env-k8s-valhalla',
      valkyrieId: 'valkyrie-valhalla-sigrun',
      summary: 'judged a signal',
      observedAt: new Date().toISOString(),
    };
    dashboard.telemetry = {
      ...dashboard.telemetry!,
      recentEvents: [recent],
    };
    renderRoster(dashboard);

    const sigrunBars = screen.getByTestId('roster-activity-valkyrie-valhalla-sigrun');
    expect(sigrunBars.querySelectorAll('[data-lit="true"]')).toHaveLength(1);
    // The event names Sigrun, so her k8s sibling Runa gets no credit.
    const runaBars = screen.getByTestId('roster-activity-valkyrie-valhalla-runa');
    expect(runaBars.querySelectorAll('[data-lit="true"]')).toHaveLength(0);
  });
});
