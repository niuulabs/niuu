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
import type { RealmSummary } from '../domain/governance';
import { RealmCard, type RealmCardProps } from './RealmCard';

const realm: RealmSummary = {
  id: 'r1',
  slug: 'lexi-api',
  name: 'Lexi API',
  sleipnir_domain: 'code',
  owner_id: null,
  instance_id: 'inst-1',
  autonomy_profile: 'balanced',
  created_at: '',
  updated_at: '',
};

function mount(props: Partial<RealmCardProps>) {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const index = createRoute({
    getParentRoute: () => rootRoute,
    path: '/',
    component: () => <RealmCard realm={realm} runningSessions={0} {...props} />,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([index]),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  });
  return render(<RouterProvider router={router} />);
}

describe('RealmCard', () => {
  afterEach(cleanup);

  it('says when nothing is bound yet', async () => {
    mount({});
    expect(await screen.findByText('nothing bound yet')).toBeInTheDocument();
    expect(screen.getByText('balanced')).toBeInTheDocument();
    expect(screen.getByText('Open ›')).toBeInTheDocument();
  });

  it('shows the running sessions', async () => {
    mount({ runningSessions: 3 });
    expect(await screen.findByText('3')).toBeInTheDocument();
    expect(screen.getByText('in sessions')).toBeInTheDocument();
  });

  it('shows what the realm is bound to', async () => {
    mount({
      binding: {
        template: 'qa-resident',
        repo: 'niuulabs/lexi-api',
        branch: 'dev',
        trackerBoard: 'LXA',
        bugBoard: 'QA',
        mountTarget: 'ymir',
      },
    });
    expect(await screen.findByText('QA workspace · niuulabs/lexi-api')).toBeInTheDocument();
    expect(screen.getByText('board LXA')).toBeInTheDocument();
    expect(screen.getByText('bugs QA')).toBeInTheDocument();
  });

  it('names the board, and counts tickets waiting with the questions', async () => {
    mount({
      binding: { template: 'blank', repo: '', branch: '', trackerBoard: 'uuid' } as never,
      boardName: 'Spring board',
      waitingTickets: 2,
    });
    expect(await screen.findByText('board Spring board')).toBeInTheDocument();
    expect(screen.getByText('Review 2')).toBeInTheDocument();
  });
});
