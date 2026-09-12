import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { createMimirMockAdapter } from '../adapters/mock';
import { renderWithMimir as render } from '../testing/renderWithMimir';
import { RegistryPage } from './RegistryPage';

describe('RegistryPage', () => {
  it('renders registered mounts from the service', async () => {
    render(<RegistryPage />, createMimirMockAdapter());
    await waitFor(() => expect(screen.getByText('Knowledge registry')).toBeInTheDocument());
    expect(screen.getByText(/\d+ instances · \d+ enabled/i)).toBeInTheDocument();
  });

  it('creates a new registry mount', async () => {
    render(<RegistryPage />, createMimirMockAdapter());
    fireEvent.click(await screen.findByRole('button', { name: '+ Add connection' }));
    await waitFor(() =>
      expect(screen.getByText('Register an existing connection')).toBeInTheDocument(),
    );

    const textboxes = screen.getAllByRole('textbox');
    fireEvent.change(textboxes[0]!, {
      target: { value: 'new-remote' },
    });
    fireEvent.change(textboxes[1]!, {
      target: { value: 'https://mimir.example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(screen.getByText('new-remote')).toBeInTheDocument());
  });
});

it('shows a managed instance only once when it is also connected', async () => {
  const service = createMimirMockAdapter();
  const entries = await service.mounts.listRegistryMounts!();
  const name = entries[0]!.name;
  service.mounts.getDeployments = async () => ({
    cluster: 'test',
    namespace: 'knowledge',
    backends: ['mimir'],
    releases: [{ name, backend: 'mimir', ready: true, message: 'Ready', target: 'local' }],
  });
  render(<RegistryPage />, service);
  expect(await screen.findByText(name)).toBeInTheDocument();
  expect(screen.getAllByText(name)).toHaveLength(1);
  expect(screen.queryByText('Deployed instances')).not.toBeInTheDocument();
  expect(screen.queryByText('Registered connections')).not.toBeInTheDocument();
  expect(await screen.findByText(/mimir · Managed · local/)).toBeInTheDocument();
});

it('opens both forms in dialogs and closes them consistently', async () => {
  render(<RegistryPage />, createMimirMockAdapter());
  fireEvent.click(await screen.findByRole('button', { name: '+ Deploy instance' }));
  expect(screen.getByRole('dialog', { name: 'Deploy instance' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Close', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '+ Add connection' }));
  expect(screen.getByRole('dialog', { name: 'Add connection' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Close', exact: true }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('confirms local deletion but only removes a shared connection', async () => {
  const service = createMimirMockAdapter();
  const entries = await service.mounts.listRegistryMounts!();
  const shared = entries[0]!;
  service.mounts.getDeployments = async () => ({
    cluster: 'local',
    namespace: 'local',
    backends: ['mimir'],
    releases: [
      {
        name: 'owned-local',
        backend: 'mimir',
        ready: true,
        message: 'Ready',
        target: 'local',
        can_delete: true,
      },
    ],
  });
  service.mounts.controlDeployment = vi.fn().mockResolvedValue({ deleted: true });
  service.mounts.deleteRegistryMount = vi.fn().mockResolvedValue(undefined);
  render(<RegistryPage />, service);
  fireEvent.click(
    within((await screen.findByText('owned-local')).closest('article')!).getByRole('button', {
      name: 'Delete',
      exact: true,
    }),
  );
  expect(service.mounts.controlDeployment).not.toHaveBeenCalled();
  fireEvent.click(
    within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete instance' }),
  );
  await waitFor(() =>
    expect(service.mounts.controlDeployment).toHaveBeenCalledWith('owned-local', 'delete', 'local'),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  const card = screen.getByText(shared.name).closest('article')!;
  fireEvent.click(within(card).getByRole('button', { name: 'Remove connection' }));
  await waitFor(() => expect(service.mounts.deleteRegistryMount).toHaveBeenCalledWith(shared.id));
  expect(service.mounts.controlDeployment).toHaveBeenCalledTimes(1);
});

it('keeps deletion visible but disabled for instances managed outside this registry', async () => {
  const service = createMimirMockAdapter();
  service.mounts.listRegistryMounts = async () => [];
  render(<RegistryPage />, service);
  await waitFor(() =>
    expect(screen.getAllByRole('button', { name: 'Inspect', exact: true }).length).toBeGreaterThan(
      0,
    ),
  );
  for (const button of screen.getAllByRole('button', { name: 'Delete', exact: true })) {
    expect(button).toBeDisabled();
  }
  expect(screen.queryByRole('button', { name: 'Inspect instance' })).not.toBeInTheDocument();
});

it('keeps identically named deployments on separate Guild targets', async () => {
  const service = createMimirMockAdapter();
  const setTweak = vi.fn();
  service.mounts.getDeployments = async () => ({
    cluster: '',
    namespace: '',
    backends: [],
    releases: ['ymir:cluster', 'noatun:cluster'].map((target) => ({
      name: 'research-brain',
      target,
      backend: 'gbrain',
      ready: true,
      message: 'Ready',
    })),
  });
  render(<RegistryPage />, service, { setTweak });
  const names = await screen.findAllByText('research-brain');
  expect(names).toHaveLength(2);
  const card =
    screen.getByText(/gbrain · Managed · noatun:cluster/).closest('article') ??
    screen.getByText(/gbrain · Managed · noatun:cluster/).parentElement!.parentElement!
      .parentElement!;
  fireEvent.click(within(card).getByRole('button', { name: 'Inspect' }));
  expect(setTweak).toHaveBeenCalledWith('mimir.deployment', {
    name: 'research-brain',
    target: 'noatun:cluster',
  });
  expect(setTweak).toHaveBeenCalledWith('activeMount', 'all');
});

it('displays tenant and global access on instance cards', async () => {
  const service = createMimirMockAdapter();
  const [base] = await service.mounts.listMounts();
  service.mounts.listRegistryMounts = async () => [];
  service.mounts.listMounts = async () => [
    { ...base!, name: 'mimir-ui', role: 'shared', accessScope: 'tenant' },
    { ...base!, name: 'mimir-global', role: 'shared', accessScope: 'global' },
  ];
  render(<RegistryPage />, service);
  expect((await screen.findByText('mimir-ui')).closest('article')).toHaveTextContent('Tenant');
  expect(screen.getByText('mimir-global').closest('article')).toHaveTextContent('Global');
});

it('opens the full dashboard for an automatically mounted Helm deployment', async () => {
  const service = createMimirMockAdapter();
  const [base] = await service.mounts.listMounts();
  const setTweak = vi.fn();
  service.mounts.listMounts = async () => [{ ...base!, name: 'gbrain-ui' }];
  service.mounts.listRegistryMounts = async () => [];
  service.mounts.getDeployments = async () => ({
    cluster: 'ymir',
    namespace: 'knowledge',
    backends: ['gbrain'],
    releases: [
      { name: 'gbrain-ui', backend: 'gbrain', target: 'cluster', ready: true, message: 'Ready' },
    ],
  });
  render(<RegistryPage />, service, { setTweak });
  await screen.findByText(/gbrain · Managed/);
  fireEvent.click(screen.getByRole('button', { name: 'Inspect' }));
  expect(setTweak).toHaveBeenCalledWith('mimir.deployment', null);
  expect(setTweak).toHaveBeenCalledWith('activeMount', 'gbrain-ui');
  expect(setTweak).toHaveBeenCalledWith('mimir.registryView', 'Analytics');
});
