import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithMimir } from '../../testing/renderWithMimir';
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

  it('keeps the operator overview in Advanced mode', async () => {
    mockMode.current = 'advanced';
    renderWithMimir(<MemoryOverviewRoute />);
    expect(await screen.findByRole('heading', { name: 'Mounts' })).toBeInTheDocument();
    expect(screen.queryByTestId('memory-home')).not.toBeInTheDocument();
  });
});
