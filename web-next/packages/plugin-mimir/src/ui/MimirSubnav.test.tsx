import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { MimirSubnav } from './MimirSubnav';
import { renderWithMimir } from '../testing/renderWithMimir';
import type { PluginCtx } from '@niuulabs/plugin-sdk';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';

const navigateMock = vi.fn();

// Mock TanStack Router hooks — subnav uses useNavigate
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}));

const mockCtx: PluginCtx = {
  tweaks: {},
  setTweak: vi.fn(),
};

const wrap = (ctx = mockCtx) => renderWithMimir(<MimirSubnav ctx={ctx} />, undefined, ctx);

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

  it('highlights the selected mount row in the expanded picker', async () => {
    wrap({ tweaks: { activeMount: 'local' }, setTweak: vi.fn() });
    await waitFor(() => expect(screen.getAllByText('local').length).toBeGreaterThan(0));
    const localRow = screen.getAllByText('local')[0]!.closest('button');
    expect(localRow).toHaveClass('mm-mount-row--active');
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
    const setTweak = vi.fn();
    wrap({ tweaks: {}, setTweak });
    await waitFor(() => expect(screen.getByText('ravn-fjolnir')).toBeInTheDocument());
    fireEvent.click(screen.getByText('ravn-fjolnir'));
    expect(setTweak).toHaveBeenCalledWith('mimir.selectedWardenId', 'ravn-fjolnir');
    expect(navigateMock).toHaveBeenCalledWith({ to: '/mimir/ravns' });
  });

  it('can collapse the Mímir subnav', async () => {
    const setTweak = vi.fn();
    wrap({ tweaks: {}, setTweak });
    fireEvent.click(screen.getByRole('button', { name: /collapse mímir sidebar/i }));
    expect(setTweak).toHaveBeenCalledWith('mimir.subnavCollapsed', true);
  });

  it('renders the collapsed Mímir subnav and can expand it', async () => {
    const setTweak = vi.fn();
    wrap({ tweaks: { 'mimir.subnavCollapsed': true }, setTweak });
    expect(screen.getByRole('button', { name: /expand mímir sidebar/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /expand mímir sidebar/i }));
    expect(setTweak).toHaveBeenCalledWith('mimir.subnavCollapsed', false);
  });

  it('marks "All mounts" active when the all-mounts filter is selected', () => {
    wrap({ tweaks: { activeMount: 'all' }, setTweak: vi.fn() });
    expect(screen.getByText('All mounts').closest('button')).toHaveClass('mm-mount-row--active');
  });

  it('supports collapsed mount and warden shortcuts', async () => {
    const setTweak = vi.fn();
    wrap({ tweaks: { 'mimir.subnavCollapsed': true, activeMount: 'local' }, setTweak });

    await waitFor(() => expect(screen.getByRole('button', { name: 'local' })).toBeInTheDocument());
    const localShortcut = screen.getByRole('button', { name: 'local' });
    expect(localShortcut.className).toContain('mm-subnav-collapsed-item--active');
    fireEvent.click(localShortcut);
    expect(setTweak).toHaveBeenCalledWith('activeMount', 'local');

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /warden ravn-fjolnir/i })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /warden ravn-fjolnir/i }));
    expect(setTweak).toHaveBeenCalledWith('mimir.selectedWardenId', 'ravn-fjolnir');
    expect(navigateMock).toHaveBeenCalledWith({ to: '/mimir/ravns' });
  });

  it('omits collapsed warden shortcuts when no wardens are bound', async () => {
    const noRavnsService: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        listRavnBindings: async () => [],
      },
    };

    renderWithMimir(
      <MimirSubnav ctx={{ tweaks: { 'mimir.subnavCollapsed': true }, setTweak: vi.fn() }} />,
      noRavnsService,
      { tweaks: { 'mimir.subnavCollapsed': true }, setTweak: vi.fn() },
    );

    expect(screen.queryByRole('button', { name: /warden /i })).not.toBeInTheDocument();
  });

  it('mutes down mounts in the mount picker', async () => {
    const downMountService: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        async listMounts() {
          return [
            {
              name: 'archive',
              role: 'shared',
              host: 'archive.local',
              url: 'http://archive.local',
              priority: 1,
              categories: null,
              status: 'down',
              pages: 1,
              sources: 1,
              lintIssues: 0,
              lastWrite: '2026-04-19T00:00:00Z',
              embedding: 'minilm',
              sizeKb: 10,
              desc: 'offline archive mount',
            },
          ];
        },
      },
    };

    renderWithMimir(<MimirSubnav ctx={mockCtx} />, downMountService, mockCtx);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /archive/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /archive/i })).toHaveClass('mm-mount-row--muted');
  });
});
