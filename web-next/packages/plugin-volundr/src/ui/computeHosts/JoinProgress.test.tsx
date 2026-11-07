import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { seedComputeHosts } from '../../adapters/computeHosts.mock';
import type { ComputeHost } from '../../domain/computeHosts';
import { JoinProgress } from './JoinProgress';

const glad = seedComputeHosts()[6]!;

function renderProgress(host: ComputeHost = glad, checking = false) {
  const onCheck = vi.fn();
  render(
    <JoinProgress host={host} poolId="valhalla-hosts" checking={checking} onCheck={onCheck} />,
  );
  return { onCheck };
}

describe('JoinProgress', () => {
  it('shows the host, its address and key, and the steps so far', () => {
    renderProgress();
    expect(screen.getByText('glad')).toBeInTheDocument();
    expect(screen.getByText('fast-disk')).toBeInTheDocument();
    expect(
      screen.getByText(/192\.168\.90\.35 · key SHA256:Rr4aMockFingerprint0cX/),
    ).toBeInTheDocument();
    const steps = within(screen.getByRole('list', { name: 'Progress' })).getAllByRole('listitem');
    expect(steps.map((step) => step.getAttribute('data-status'))).toEqual([
      'done',
      'done',
      'now',
      'todo',
      'todo',
      'todo',
    ]);
    expect(steps[2]).toHaveTextContent('Preparing · recipe host@2026-11-07');
  });

  it('echoes what the join command printed on the host', () => {
    renderProgress();
    expect(screen.getByText(/✓ registered as glad with valhalla-hosts/)).toBeInTheDocument();
    expect(screen.getByText(/To undo: sudo niuu-host-leave/)).toBeInTheDocument();
  });

  it('names the connector when the host has no address', () => {
    renderProgress({ ...glad, connection: { ...glad.connection, target: '' } });
    expect(screen.getByText(/direct_ssh · key/)).toBeInTheDocument();
  });

  it('shows a quarantined host findings and lets the operator check again', () => {
    const quarantined: ComputeHost = {
      ...glad,
      state: 'quarantined',
      health: 'quarantined',
      findings: ['Nix daemon failed to start'],
    };
    const { onCheck } = renderProgress(quarantined);
    expect(screen.getByRole('alert')).toHaveTextContent('Nix daemon failed to start');
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
    expect(onCheck).toHaveBeenCalled();
    expect(
      within(screen.getByRole('list', { name: 'Progress' })).getAllByRole('listitem')[2],
    ).toHaveAttribute('data-status', 'failed');
  });

  it('holds Check now while a check runs and hides it for a healthy host', () => {
    const lost: ComputeHost = { ...glad, state: 'unreachable', health: 'unreachable' };
    const { rerender } = render(<JoinProgress host={lost} poolId="p" checking onCheck={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Check now' })).toBeDisabled();
    rerender(<JoinProgress host={glad} poolId="p" checking={false} onCheck={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Check now' })).toBeNull();
  });
});
