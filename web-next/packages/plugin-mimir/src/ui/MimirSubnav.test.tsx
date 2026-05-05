import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { MimirSubnav } from './MimirSubnav';
import { renderWithMimir } from '../testing/renderWithMimir';
import type { PluginCtx } from '@niuulabs/plugin-sdk';

// Mock TanStack Router hooks — subnav uses useNavigate
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}));

const mockCtx: PluginCtx = {
  tweaks: {},
  setTweak: vi.fn(),
};

const wrap = (ctx = mockCtx) => renderWithMimir(<MimirSubnav ctx={ctx} />);

describe('MimirSubnav', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the mount focus section', () => {
    wrap();
    expect(screen.getByText('Mount focus')).toBeInTheDocument();
  });

  it('renders "All mounts" button', () => {
    wrap();
    expect(screen.getByText('All mounts')).toBeInTheDocument();
  });

  it('does not render a Navigation section (tabs are in topbar)', () => {
    wrap();
    expect(screen.queryByText('Navigation')).not.toBeInTheDocument();
  });

  it('renders quick filters section', () => {
    wrap();
    expect(screen.getByText('Quick filters')).toBeInTheDocument();
    expect(screen.getByText('Errors')).toBeInTheDocument();
    expect(screen.getByText('Low confidence')).toBeInTheDocument();
  });

  it('renders per-mount rows after data loads', async () => {
    wrap();
    // Mock data has mounts: local, shared, platform
    // Both name and role cells show 'local' for the first mount, so use getAllByText
    await waitFor(() => expect(screen.getAllByText('local').length).toBeGreaterThan(0));
  });

  it('renders wardens roster when ravns are present', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText('Wardens')).toBeInTheDocument());
    // ravns from mock: ravn-fjolnir, ravn-skald
    await waitFor(() => expect(screen.getByText('ravn-fjolnir')).toBeInTheDocument());
  });

  it('clicking "All mounts" calls setTweak with "all"', () => {
    const setTweak = vi.fn();
    wrap({ tweaks: {}, setTweak });
    fireEvent.click(screen.getByText('All mounts'));
    expect(setTweak).toHaveBeenCalledWith('activeMount', 'all');
  });

  it('mount row is active when ctx.tweaks.activeMount matches', () => {
    wrap({ tweaks: { activeMount: 'all' }, setTweak: vi.fn() });
    const allBtn = screen.getByText('All mounts').closest('button');
    expect(allBtn).toHaveAttribute('aria-pressed', 'true');
  });

  it('clicking a per-mount row calls setTweak with the mount name', async () => {
    const setTweak = vi.fn();
    wrap({ tweaks: {}, setTweak });
    await waitFor(() => expect(screen.getAllByText('local').length).toBeGreaterThan(0));
    const localBtn = screen.getAllByText('local')[0]!.closest('button')!;
    fireEvent.click(localBtn);
    expect(setTweak).toHaveBeenCalledWith('activeMount', 'local');
  });

  it('clicking Errors navigates', async () => {
    wrap();
    fireEvent.click(screen.getByText('Errors'));
  });

  it('clicking Flagged navigates', async () => {
    wrap();
    fireEvent.click(screen.getByText('Flagged'));
  });

  it('clicking Low confidence navigates', async () => {
    wrap();
    fireEvent.click(screen.getByText('Low confidence'));
  });

  it('clicking a warden navigates', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText('ravn-fjolnir')).toBeInTheDocument());
    fireEvent.click(screen.getByText('ravn-fjolnir'));
  });
});
