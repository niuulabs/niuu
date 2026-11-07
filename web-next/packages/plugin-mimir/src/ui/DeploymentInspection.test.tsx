import { fireEvent, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { createMimirMockAdapter } from '../adapters/mock';
import { renderWithMimir } from '../testing/renderWithMimir';
import { DeploymentInspection } from './DeploymentInspection';

it('selects actual process output and controls the deployment', async () => {
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
    logs: { stdout: '', stderr: 'Server listening' },
  });
  service.mounts.controlDeployment = vi.fn().mockResolvedValue({});
  renderWithMimir(<DeploymentInspection instanceName="brain" />, service);
  expect(await screen.findByText('gbrain runtime')).toBeInTheDocument();
  expect(await screen.findByText('Process logs')).toBeInTheDocument();
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
