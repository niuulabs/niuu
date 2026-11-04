import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router';
import type { Mount } from '@niuulabs/domain';
import type { ResidentMemoryEntry } from '../domain/join';
import { ResidentMemoryList } from './ResidentMemoryList';

function mount(entries: ResidentMemoryEntry[]) {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const index = createRoute({
    getParentRoute: () => rootRoute,
    path: '/',
    component: () => <ResidentMemoryList residentName="Regin" entries={entries} />,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([index]),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  });
  return render(<RouterProvider router={router} />);
}

describe('ResidentMemoryList', () => {
  afterEach(cleanup);

  it('says the resident has not reported its memory', async () => {
    mount([]);
    expect(await screen.findByText('No workspace memory')).toBeInTheDocument();
    expect(
      screen.getByText(/Regin has not reported where it keeps its memory/),
    ).toBeInTheDocument();
  });

  it('opens a served store in Memory by its platform mount', async () => {
    mount([
      {
        store: { name: 'local', role: 'local', platformMount: 'regin' },
        mount: { name: 'regin', pages: 12, status: 'healthy' } as Mount,
      },
    ]);
    const link = await screen.findByRole('link', { name: 'Explore in Memory' });
    expect(link.getAttribute('href')).toBe('/mimir?mount=regin');
    expect(screen.getByText('12 pages · healthy')).toBeInTheDocument();
  });

  it('says which stores the platform does not serve or has not discovered', async () => {
    mount([
      { store: { name: 'local', role: 'local', platformMount: '' }, mount: null },
      { store: { name: 'research', role: 'domain', platformMount: 'research' }, mount: null },
    ]);
    expect(
      await screen.findByText('kept by the resident; the platform does not serve it'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('mount research is not discovered on this platform'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('link')).toBeNull();
  });
});
