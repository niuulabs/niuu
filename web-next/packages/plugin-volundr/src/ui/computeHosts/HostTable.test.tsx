import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { seedComputeHosts } from '../../adapters/computeHosts.mock';
import type { ComputeHost } from '../../domain/computeHosts';
import { HostTable } from './HostTable';

const hosts = seedComputeHosts();
const NOW = Date.parse('2026-11-06T12:00:00Z');

function renderTable(list: ComputeHost[] = hosts, busy: ReadonlySet<string> = new Set()) {
  const onAction = vi.fn();
  const onTagsChange = vi.fn();
  render(
    <HostTable
      hosts={list}
      now={NOW}
      busy={busy}
      onAction={onAction}
      onTagsChange={onTagsChange}
    />,
  );
  return { onAction, onTagsChange };
}

function row(name: string) {
  return screen.getByTestId(`host-row-${name}`);
}

describe('HostTable', () => {
  it('shows each host with its subline, size and connection', () => {
    renderTable();
    const hrimfaxi = within(row('hrimfaxi'));
    expect(hrimfaxi.getByText('Host · checked 2m ago')).toBeInTheDocument();
    expect(hrimfaxi.getByText('In service')).toBeInTheDocument();
    expect(hrimfaxi.getByText('64 CPU · 256 GiB')).toBeInTheDocument();
    expect(hrimfaxi.getByText('SSH · 192.168.90.31')).toBeInTheDocument();
    expect(hrimfaxi.getByText('12 used · 3 free of 15')).toBeInTheDocument();
    expect(within(row('gloi')).getByText('Host · exclusive')).toBeInTheDocument();
    expect(within(row('gloi')).getByText('24 CPU · 96 GiB · 1 GPU')).toBeInTheDocument();
  });

  it('draws one cell per slot with what holds it', () => {
    renderTable();
    const cells = [...row('hrimfaxi').querySelectorAll('[data-cell]')].map((cell) =>
      cell.getAttribute('data-cell'),
    );
    expect(cells).toHaveLength(15);
    expect(cells.filter((cell) => cell === 'busy')).toHaveLength(11);
    expect(cells).toContain('starting');
    expect(row('alsvin').querySelector('[data-cell="failed"]')).not.toBeNull();
  });

  it('says a joining host has no slot count yet and has no slot bar', () => {
    renderTable();
    expect(within(row('glad')).getByText('set after first check')).toBeInTheDocument();
    expect(within(row('glad')).getByText('Preparing')).toBeInTheDocument();
    expect(row('glad').querySelector('[data-cell]')).toBeNull();
  });

  it('notes that a draining host is removed when empty', () => {
    renderTable();
    expect(within(row('arvak')).getByText('Draining')).toBeInTheDocument();
    expect(within(row('arvak')).getByText('then removed')).toBeInTheDocument();
    expect(within(row('arvak')).getByText('2 sessions left · no new ones')).toBeInTheDocument();
  });

  it('shows the findings of a quarantined host inline', () => {
    renderTable();
    const finding = screen.getByTestId('findings-alsvin');
    expect(finding).toHaveTextContent('takes no new sessions until it passes a check');
    expect(
      within(finding).getByText('node vite (pid 48211) still running as niuu-slot-4'),
    ).toBeInTheDocument();
    expect(finding).toHaveTextContent('Fix it on the host, then Check now.');
  });

  it('explains a disconnected host and that its sessions keep running', () => {
    renderTable();
    const finding = screen.getByTestId('findings-ljosi');
    expect(finding).toHaveTextContent('Forge lost the connection: connection reset.');
    expect(finding).toHaveTextContent('Its 2 sessions keep running there');
    expect(within(row('ljosi')).getByText('Disconnected')).toBeInTheDocument();
    expect(within(row('ljosi')).getByText('Outbound tunnel · down 9m')).toBeInTheDocument();
  });

  it('words a disconnected host without sessions or an error', () => {
    const lost: ComputeHost = {
      ...hosts[5]!,
      sessions: [],
      slotsFree: 4,
      findings: [],
      connection: { ...hosts[5]!.connection, lastError: null },
    };
    renderTable([lost]);
    expect(screen.getByTestId('findings-ljosi')).toHaveTextContent(
      'Forge has no connection to this host. Nothing new is placed on it.',
    );
  });

  it('says one session in the singular', () => {
    const one: ComputeHost = { ...hosts[5]!, sessions: hosts[5]!.sessions.slice(0, 1) };
    renderTable([one]);
    expect(screen.getByTestId('findings-ljosi')).toHaveTextContent('Its 1 session keeps running');
  });

  it('shows no findings for a healthy host', () => {
    renderTable([hosts[0]!]);
    expect(screen.queryByTestId('findings-hrimfaxi')).toBeNull();
  });

  it('asks for the action the operator picked', () => {
    const { onAction } = renderTable();
    fireEvent.click(screen.getByRole('button', { name: 'Check now hrimfaxi' }));
    expect(onAction).toHaveBeenLastCalledWith(hosts[0], 'check');
    fireEvent.click(screen.getByRole('button', { name: 'Drain hrimfaxi' }));
    expect(onAction).toHaveBeenLastCalledWith(hosts[0], 'drain');
    fireEvent.click(screen.getByRole('button', { name: 'Drain and remove skinfaxi' }));
    expect(onAction).toHaveBeenLastCalledWith(hosts[1], 'drain_remove');
    fireEvent.click(screen.getByRole('button', { name: 'Open glad' }));
    expect(onAction).toHaveBeenLastCalledWith(hosts[6], 'open');
  });

  it('offers resume and remove on an out-of-service host', () => {
    const out: ComputeHost = { ...hosts[1]!, state: 'out_of_service', sessions: [] };
    const { onAction } = renderTable([out]);
    fireEvent.click(screen.getByRole('button', { name: 'Resume skinfaxi' }));
    expect(onAction).toHaveBeenLastCalledWith(out, 'resume');
    fireEvent.click(screen.getByRole('button', { name: 'Remove skinfaxi' }));
    expect(onAction).toHaveBeenLastCalledWith(out, 'remove');
  });

  it('disables the controls of a host with a change in flight', () => {
    renderTable(hosts, new Set([hosts[0]!.id]));
    expect(screen.getByRole('button', { name: 'Check now hrimfaxi' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Add tag to hrimfaxi' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Check now skinfaxi' })).toBeEnabled();
  });

  it('says when no host matches', () => {
    renderTable([]);
    expect(screen.getByText('No hosts match.')).toBeInTheDocument();
  });

  describe('inline tag editing', () => {
    it('adds a tag with Enter', () => {
      const { onTagsChange } = renderTable();
      fireEvent.click(screen.getByRole('button', { name: 'Add tag to skinfaxi' }));
      const input = screen.getByLabelText('New tag for skinfaxi');
      fireEvent.change(input, { target: { value: 'gpu arm64' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      expect(onTagsChange).toHaveBeenCalledWith(hosts[1], ['site:hnoss', 'gpu', 'arm64']);
      expect(screen.queryByLabelText('New tag for skinfaxi')).toBeNull();
    });

    it('adds a tag when the input loses focus', () => {
      const { onTagsChange } = renderTable();
      fireEvent.click(screen.getByRole('button', { name: 'Add tag to skinfaxi' }));
      const input = screen.getByLabelText('New tag for skinfaxi');
      fireEvent.change(input, { target: { value: 'gpu' } });
      fireEvent.blur(input);
      expect(onTagsChange).toHaveBeenCalledWith(hosts[1], ['site:hnoss', 'gpu']);
    });

    it('drops the entry on Escape and ignores a tag the host already has', () => {
      const { onTagsChange } = renderTable();
      fireEvent.click(screen.getByRole('button', { name: 'Add tag to skinfaxi' }));
      fireEvent.change(screen.getByLabelText('New tag for skinfaxi'), {
        target: { value: 'gpu' },
      });
      fireEvent.keyDown(screen.getByLabelText('New tag for skinfaxi'), { key: 'Escape' });
      expect(onTagsChange).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('button', { name: 'Add tag to skinfaxi' }));
      fireEvent.change(screen.getByLabelText('New tag for skinfaxi'), {
        target: { value: 'site:hnoss' },
      });
      fireEvent.keyDown(screen.getByLabelText('New tag for skinfaxi'), { key: 'Enter' });
      expect(onTagsChange).not.toHaveBeenCalled();
    });

    it('removes a tag', () => {
      const { onTagsChange } = renderTable();
      fireEvent.click(screen.getByRole('button', { name: 'Remove tag fast-disk from hrimfaxi' }));
      expect(onTagsChange).toHaveBeenCalledWith(hosts[0], ['site:hnoss']);
    });
  });
});
