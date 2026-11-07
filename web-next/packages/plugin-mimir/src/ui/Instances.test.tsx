import { expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { renderWithMimir } from '../testing/renderWithMimir';
import { createMimirMockAdapter } from '../adapters/mock';
import { InstanceInspection } from './InstanceInspection';

it('shows backend-specific metrics and unavailable fields', async () => {
  const service = createMimirMockAdapter();
  service.mounts.inspectInstances = async () => [
    {
      mount: 'research',
      backend: 'gbrain',
      metrics: { 'Storage engine': 'postgres' },
      unavailable: ['Write history'],
    },
  ];
  renderWithMimir(<InstanceInspection />, service);
  expect(await screen.findByText('research · gbrain')).toBeInTheDocument();
  expect(screen.getAllByText('postgres').length).toBeGreaterThan(0);
  expect(screen.getByText('Write history')).toBeInTheDocument();
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
