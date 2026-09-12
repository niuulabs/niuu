import { expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { renderWithMimir } from '../testing/renderWithMimir';
import { createMimirMockAdapter } from '../adapters/mock';
import { InstanceInspection } from './InstanceInspection';
import { InstanceDeployments } from './InstanceDeployments';

it('shows backend-specific metrics and unavailable fields', async () => {
  const service = createMimirMockAdapter();
  service.mounts.inspectInstances = async () => [
    {
      mount: 'research',
      backend: 'gbrain',
      metrics: { 'Storage engine': 'postgres' },
      unavailable: ['Dream history'],
    },
  ];
  renderWithMimir(<InstanceInspection />, service);
  expect(await screen.findByText('research · gbrain')).toBeInTheDocument();
  expect(screen.getAllByText('postgres').length).toBeGreaterThan(0);
  expect(screen.getByText('Dream history')).toBeInTheDocument();
});

it('deploys an instance with attached maintenance using target defaults', async () => {
  const service = createMimirMockAdapter();
  service.mounts.getDeployments = async () => ({
    cluster: 'test',
    namespace: 'knowledge',
    backends: ['mimir', 'gbrain'],
    target: 'local',
    warden_available: true,
    releases: [],
  });
  service.mounts.deployInstance = vi.fn().mockResolvedValue({ ready: false });
  renderWithMimir(<InstanceDeployments />, service);
  await screen.findByLabelText('Instance name');
  fireEvent.change(screen.getByLabelText('Instance name'), { target: { value: 'research' } });
  fireEvent.click(screen.getByLabelText('Attach a warden'));
  fireEvent.click(screen.getByRole('button', { name: 'Deploy instance' }));
  await screen.findByText(/Deployment requested/);
  expect(service.mounts.deployInstance).toHaveBeenCalledWith({
    name: 'research',
    backend: 'mimir',
    warden: true,
    target: 'local',
  });
});

it('reports deployment errors', async () => {
  const service = createMimirMockAdapter();
  service.mounts.getDeployments = async () => {
    throw new Error('Forbidden');
  };
  service.mounts.deployInstance = vi.fn();
  renderWithMimir(<InstanceDeployments />, service);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Deployment targets could not be loaded.',
  );
});

it('switches instance dashboards and scopes graph requests', async () => {
  const service = createMimirMockAdapter();
  service.mounts.inspectInstances = async () =>
    ['first', 'second'].map((mount) => ({
      mount,
      backend: 'gbrain',
      metrics: { Pages: 2 },
      unavailable: [],
    }));
  service.pages.getGraph = vi.fn().mockResolvedValue({
    nodes: [
      { id: 'a', category: 'research' },
      { id: 'b', category: 'notes' },
    ],
    edges: [{ source: 'a', target: 'b', type: 'wikilink' }],
  });
  renderWithMimir(<InstanceInspection />, service);
  await screen.findByRole('button', { name: /second/ });
  fireEvent.click(screen.getByRole('button', { name: /second/ }));
  await screen.findByLabelText('second overview');
  expect(service.pages.getGraph).toHaveBeenCalledWith({ mountName: 'second' });
  expect(await screen.findByRole('img', { name: '2 of 2 pages connected' })).toBeInTheDocument();
});

it('replaces the warden option with native dreams when switching to gbrain', async () => {
  const service = createMimirMockAdapter();
  service.mounts.getDeployments = async () => ({
    cluster: 'test',
    namespace: 'knowledge',
    backends: ['mimir', 'gbrain'],
    target: 'local',
    warden_available: true,
    dream_available: true,
    releases: [],
  });
  service.mounts.deployInstance = vi.fn().mockResolvedValue({ ready: false });
  renderWithMimir(<InstanceDeployments />, service);
  fireEvent.click(await screen.findByLabelText('Attach a warden'));
  fireEvent.click(screen.getByRole('button', { name: /gbrain/i }));
  expect(screen.queryByLabelText('Attach a warden')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Enable scheduled dream cycles')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Instance name'), { target: { value: 'research' } });
  fireEvent.click(screen.getByRole('button', { name: 'Deploy instance' }));
  await screen.findByText(/Deployment requested/);
  expect(service.mounts.deployInstance).toHaveBeenCalledWith(
    expect.objectContaining({
      backend: 'gbrain',
      warden: false,
    }),
  );
});

it('submits optional warden settings without asking for credentials', async () => {
  const service = createMimirMockAdapter();
  service.mounts.getDeployments = async () => ({
    cluster: 'local',
    namespace: 'local',
    target: 'local',
    backends: ['mimir'],
    warden_available: true,
    releases: [],
  });
  service.mounts.deployInstance = vi.fn().mockResolvedValue({ ready: false });
  renderWithMimir(<InstanceDeployments />, service);
  fireEvent.click(await screen.findByLabelText('Attach a warden'));
  fireEvent.change(screen.getByLabelText('Instance name'), { target: { value: 'custom' } });
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'claude-sonnet-4-6' } });
  fireEvent.change(screen.getByLabelText('Persona'), { target: { value: 'research' } });
  fireEvent.change(screen.getByLabelText('Warden dream schedule (cron)'), {
    target: { value: '0 5 * * *' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Deploy instance' }));
  await screen.findByText(/Deployment requested/);
  expect(service.mounts.deployInstance).toHaveBeenCalledWith(
    expect.objectContaining({
      warden_overrides: {
        model: 'claude-sonnet-4-6',
        persona: 'research',
        dream_cycle_cron_expression: '0 5 * * *',
      },
    }),
  );
});
