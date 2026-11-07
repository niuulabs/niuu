import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ApiClientError } from '@niuulabs/query';
import { seedComputeHosts } from '../../adapters/computeHosts.mock';
import { HostRemovalDialog, type RemovalMode } from './HostRemovalDialog';

const [, skinfaxi, , , , , glad] = seedComputeHosts();

function renderDialog(
  mode: RemovalMode,
  overrides: Partial<Parameters<typeof HostRemovalDialog>[0]> = {},
) {
  const props = {
    host: skinfaxi!,
    mode,
    onDrain: vi.fn().mockResolvedValue(undefined),
    onRemove: vi.fn().mockResolvedValue({ removed: true, hostCleanupCommand: null }),
    onClose: vi.fn(),
    ...overrides,
  };
  render(<HostRemovalDialog {...props} />);
  return props;
}

describe('HostRemovalDialog', () => {
  it('says running sessions are never stopped', () => {
    renderDialog('drain');
    expect(screen.getByRole('dialog', { name: 'Take skinfaxi out?' })).toBeInTheDocument();
    expect(
      screen.getByText('3 sessions are running on it. They are never stopped.'),
    ).toBeInTheDocument();
  });

  it('words one session and none', () => {
    renderDialog('drain', { host: { ...skinfaxi!, sessions: skinfaxi!.sessions.slice(0, 1) } });
    expect(
      screen.getByText('1 session is running on it. They are never stopped.'),
    ).toBeInTheDocument();
  });

  it('says no sessions run on an empty host', () => {
    renderDialog('drain', { host: glad! });
    expect(screen.getByText('No sessions are running on it.')).toBeInTheDocument();
  });

  it('drains by default', async () => {
    const props = renderDialog('drain');
    expect(screen.getByRole('radio', { name: /^Drain\s*No new sessions/ })).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Drain' }));
    await waitFor(() => expect(props.onClose).toHaveBeenCalled());
    expect(props.onDrain).toHaveBeenCalledWith(false);
  });

  it('drains and removes when that is chosen, and opens with it when asked', async () => {
    const props = renderDialog('drain_remove');
    expect(screen.getByRole('radio', { name: /Drain and remove/ })).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Drain and remove' }));
    await waitFor(() => expect(props.onClose).toHaveBeenCalled());
    expect(props.onDrain).toHaveBeenCalledWith(true);
  });

  it('switches between the two with the radios', () => {
    renderDialog('drain');
    fireEvent.click(screen.getByRole('radio', { name: /Drain and remove/ }));
    expect(screen.getByRole('button', { name: 'Drain and remove' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: /^Drain\s*No new sessions/ }));
    expect(screen.getByRole('button', { name: 'Drain' })).toBeInTheDocument();
  });

  it('shows why a drain was refused and stays open', async () => {
    const props = renderDialog('drain', {
      onDrain: vi.fn().mockRejectedValue(new ApiClientError('failed', 409, 'host is busy')),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Drain' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('host is busy');
    expect(props.onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Drain' })).toBeEnabled();
  });

  it('closes on Cancel', () => {
    const props = renderDialog('drain');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(props.onClose).toHaveBeenCalled();
  });

  it('points at the command that cleans the host itself', () => {
    renderDialog('drain');
    expect(screen.getByText('sudo niuu-host-leave')).toBeInTheDocument();
  });

  it('removes an empty host and says the key is gone', async () => {
    const props = renderDialog('remove', { host: glad! });
    expect(screen.getByRole('dialog', { name: 'Remove glad?' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    expect(await screen.findByTestId('remove-result')).toHaveTextContent(
      'glad was removed.Forge removed its key from the host.',
    );
    expect(props.onRemove).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(props.onClose).toHaveBeenCalled();
  });

  it('shows the cleanup command when Forge could not clean the host', async () => {
    renderDialog('remove', {
      host: glad!,
      onRemove: vi
        .fn()
        .mockResolvedValue({ removed: true, hostCleanupCommand: 'sudo niuu-host-leave' }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    const result = await screen.findByTestId('remove-result');
    expect(result).toHaveTextContent('Forge could not clean the host itself');
    expect(result).toHaveTextContent('sudo niuu-host-leave');
    expect(screen.getByRole('button', { name: 'Copy' })).toBeInTheDocument();
  });

  it('shows why a removal was refused', async () => {
    renderDialog('remove', {
      host: glad!,
      onRemove: vi.fn().mockRejectedValue(new ApiClientError('failed', 409, 'sessions still run')),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('sessions still run');
  });
});
