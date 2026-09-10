import { fireEvent, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { createMimirMockAdapter } from '../adapters/mock';
import { renderWithMimir } from '../testing/renderWithMimir';
import { DeploymentInspection } from './DeploymentInspection';

it('shows native phase outcomes and selects actual process output', async () => {
  const service = createMimirMockAdapter();
  service.mounts.getDeployments = async () => ({
    cluster: 'local',
    namespace: 'local',
    backends: ['gbrain'],
    releases: [
      { name: 'brain', backend: 'gbrain', ready: true, message: 'Running', target: 'local' },
    ],
  });
  service.mounts.inspectDeployment = async () => ({
    name: 'brain',
    ready: true,
    message: 'Running',
    dream: { enabled: true, schedule: '0 2 * * *', phases: ['lint', 'orphans'] },
    logs: { stdout: '', stderr: 'Server listening', dream: 'native JSON output' },
    dream_results: [
      {
        timestamp: '2026-09-10T01:00:00Z',
        status: 'clean',
        duration_ms: 25,
        phases: [
          { phase: 'lint', status: 'skipped', details: { reason: 'no_brain_dir' } },
          { phase: 'orphans', status: 'ok', summary: '0 orphan pages' },
        ],
      },
    ],
  });
  service.mounts.controlDeployment = vi.fn().mockResolvedValue({});
  renderWithMimir(<DeploymentInspection instanceName="brain" />, service);
  expect(await screen.findByText('0 orphan pages')).toBeInTheDocument();
  expect(screen.getByText(/Skipped: no filesystem checkout/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Start', exact: true })).toBeDisabled();
  fireEvent.click(screen.getByText('Process logs'));
  expect(screen.getByText('Server listening')).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: 'Log stream' })).toHaveTextContent(
    'Process output (stderr)',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Stop', exact: true }));
  await waitFor(() =>
    expect(service.mounts.controlDeployment).toHaveBeenCalledWith('brain', 'stop', 'local'),
  );
});

it('inspects the selected target when clusters reuse an instance name', async () => {
  const service = createMimirMockAdapter();
  service.mounts.getDeployments = async () => ({
    cluster: '',
    namespace: '',
    backends: [],
    releases: ['ymir', 'noatun'].map((target) => ({
      name: 'brain',
      target,
      backend: 'gbrain',
      ready: true,
      message: 'Ready',
    })),
  });
  service.mounts.inspectDeployment = vi
    .fn()
    .mockResolvedValue({ name: 'brain', ready: true, message: 'Ready', logs: {} });
  renderWithMimir(<DeploymentInspection instanceName="brain" target="noatun" />, service);
  await waitFor(() =>
    expect(service.mounts.inspectDeployment).toHaveBeenCalledWith('brain', 'noatun'),
  );
});
