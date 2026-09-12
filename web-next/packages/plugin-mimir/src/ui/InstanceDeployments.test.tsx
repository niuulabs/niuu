import { fireEvent, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { createMimirMockAdapter } from '../adapters/mock';
import { renderWithMimir } from '../testing/renderWithMimir';
import { InstanceDeployments } from './InstanceDeployments';

it('uses a healthy Guild target and sends its full target identity', async () => {
  const service = createMimirMockAdapter();
  service.mounts.getDeployments = async () => ({
    cluster: '',
    namespace: '',
    backends: [],
    releases: [],
    targets: [
      {
        id: 'offline:',
        source_name: 'Offline service',
        cluster: '',
        namespace: '',
        backends: [],
        releases: [],
        error: 'Unavailable',
      },
      {
        id: 'ymir:cluster',
        source_name: 'Mimir Yggdrasil',
        cluster: 'ymir',
        namespace: 'knowledge',
        backends: ['mimir', 'gbrain'],
        releases: [],
      },
    ],
  });
  service.mounts.deployInstance = vi.fn().mockResolvedValue({});
  renderWithMimir(<InstanceDeployments />, service);
  expect(await screen.findByRole('combobox', { name: 'Deploy to' })).toHaveTextContent(
    'Mimir Yggdrasil · ymir',
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'Instance name' }), {
    target: { value: 'research' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Deploy instance', exact: true }));
  await waitFor(() =>
    expect(service.mounts.deployInstance).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'research', target: 'ymir:cluster' }),
    ),
  );
});

it('explains when Guild has no deployment services', async () => {
  const service = createMimirMockAdapter();
  service.mounts.getDeployments = async () => ({
    cluster: '',
    namespace: '',
    backends: [],
    releases: [],
    targets: [],
  });
  renderWithMimir(<InstanceDeployments />, service);
  expect(
    await screen.findByText(/No deployment services are available in your Guild/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Deploy instance', exact: true }),
  ).not.toBeInTheDocument();
});
