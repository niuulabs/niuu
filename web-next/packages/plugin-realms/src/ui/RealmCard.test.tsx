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
import type { Ravn } from '@niuulabs/plugin-ravn';
import type { RealmSummary, ValkyrieResident } from '@niuulabs/plugin-valkyrie';
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
    component: () => (
      <RealmCard
        realm={realm}
        resident={null}
        ravn={null}
        pendingReviews={[]}
        runningSessions={0}
        {...props}
      />
    ),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([index]),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  });
  return render(<RouterProvider router={router} />);
}

describe('RealmCard', () => {
  afterEach(cleanup);

  it('says when a realm has no resident yet', async () => {
    mount({});
    expect(await screen.findByText('no resident yet')).toBeInTheDocument();
    expect(screen.getByText('balanced')).toBeInTheDocument();
    expect(screen.getByText('Open ›')).toBeInTheDocument();
    expect(screen.getByText('nothing bound yet')).toBeInTheDocument();
  });

  it('shows the ravn status when only the fleet knows the resident', async () => {
    mount({ ravn: { status: 'active' } as Ravn });
    expect(await screen.findByText('active')).toBeInTheDocument();
  });

  it('shows wakefulness, tools, last action and the review count', async () => {
    const resident = {
      wakefulness: 'dreaming',
      toolCount: 4,
      lastActionAt: '2026-09-13T09:41:00Z',
    } as ValkyrieResident;
    mount({
      resident,
      pendingReviews: [{ itemId: 'a' }, { itemId: 'b' }] as never,
      runningSessions: 3,
    });
    expect(await screen.findByText('Review 2')).toBeInTheDocument();
    expect(screen.getByText('tools')).toBeInTheDocument();
    expect(screen.getByText(/last acted .* ago/)).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('shows what the realm is bound to and the environment health', async () => {
    mount({
      binding: {
        template: 'qa-resident',
        repo: 'niuulabs/lexi-api',
        branch: 'dev',
        trackerBoard: 'LXA',
        bugBoard: 'QA',
        mountTarget: 'ymir',
      },
      environment: { health: 'degraded', unresolvedSignalCount: 2 } as never,
    });
    expect(await screen.findByText('QA resident · niuulabs/lexi-api')).toBeInTheDocument();
    expect(screen.getByText('board LXA')).toBeInTheDocument();
    expect(screen.getByText('bugs QA')).toBeInTheDocument();
    expect(screen.getByText('2 open signals')).toBeInTheDocument();
  });

  it('falls back to an unknown dot for an unexpected wakefulness value', async () => {
    mount({ resident: { wakefulness: 'odd', toolCount: 0 } as unknown as ValkyrieResident });
    expect(await screen.findByTestId('realm-card-lexi-api')).toBeInTheDocument();
  });
});
