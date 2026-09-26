import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithMimir } from '../../testing/renderWithMimir';
import { createFakeMimirService } from '../../testing/fakeMimirService';
import { MemoryOverviewRoute } from './MemoryOverviewRoute';

const mockMode = vi.hoisted(() => ({ current: 'simple' as 'simple' | 'advanced' }));

vi.mock('@niuulabs/shell', () => ({
  useUiMode: () => mockMode.current,
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useSearch: () => ({}),
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));

describe('MemoryOverviewRoute', () => {
  beforeEach(() => {
    mockMode.current = 'simple';
  });

  it('renders the memory home in Simple mode', async () => {
    renderWithMimir(<MemoryOverviewRoute />);
    expect(await screen.findByTestId('memory-home')).toBeInTheDocument();
  });

  it('renders the Memory Explore scene in Advanced mode', async () => {
    // MemoryExploreView needs the extended GraphNode fields (firstSeen,
    // confidence) and getLiveActivity that the shared mock adapter does not
    // implement yet (an out-of-scope adapter) — use a fake service instead,
    // per this build's testing guidance.
    mockMode.current = 'advanced';
    renderWithMimir(<MemoryOverviewRoute />, createFakeMimirService());
    expect(await screen.findByTestId('memory-explore-view')).toBeInTheDocument();
    expect(screen.queryByTestId('memory-home')).not.toBeInTheDocument();
  });
});
