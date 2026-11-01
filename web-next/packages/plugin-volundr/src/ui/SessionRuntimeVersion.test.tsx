import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { createMockVolundrService } from '../adapters/mock';
import type { VolundrSession } from '../models/volundr.model';
import type { RuntimeVersion } from '../ports/IVolundrService';
import { SessionRuntimeVersion } from './SessionRuntimeVersion';

function setup(probe: () => Promise<RuntimeVersion>) {
  const service = { ...createMockVolundrService(), getRuntimeVersion: vi.fn(probe) };
  const session = { id: 's', instanceId: 'build', status: 'running' } as VolundrSession;
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <SessionRuntimeVersion session={session} service={service} />
    </QueryClientProvider>,
  );
  return service;
}

it('shows the actual mismatch and lets the user inspect it without restarting', async () => {
  const service = setup(async () => ({
    state: 'different',
    current: { revision: 'old-build' },
    available: { revision: 'new-build' },
  }));
  fireEvent.click(await screen.findByRole('button', { name: 'Runtime update available' }));
  expect(screen.getByText(/It has not been restarted/)).toBeInTheDocument();
  expect(screen.getByText('old-buil')).toBeInTheDocument();
  expect(screen.getByText('new-buil')).toBeInTheDocument();
  expect(service.getRuntimeVersion).toHaveBeenCalledWith('s', 'build');
  fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
  expect(await screen.findByText(/wait for its work to finish/)).toBeInTheDocument();
});

it.each(['current', 'not_running', 'unknown', 'unavailable'] as const)(
  'reports %s without claiming a new version',
  async (state) => {
    setup(async () => ({ state, current: null, available: null }));
    fireEvent.click(screen.getByRole('button', { name: 'Runtime version' }));
    expect(await screen.findByRole('button', { name: 'Check again' })).toBeInTheDocument();
    expect(screen.queryByText('Runtime update available')).not.toBeInTheDocument();
  },
);

it('renders loading and an honest unavailable state', async () => {
  let reject!: (reason: Error) => void;
  setup(
    () =>
      new Promise((_resolve, fail) => {
        reject = fail;
      }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Runtime version' }));
  expect(screen.getByText('Checking runtime version…')).toBeInTheDocument();
  reject(new Error('old host'));
  expect(
    await screen.findByText('This Forge could not report its runtime version.'),
  ).toBeInTheDocument();
});
