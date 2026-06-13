import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within, fireEvent } from '@testing-library/react';
import { renderWithMimir as wrap } from '../testing/renderWithMimir';
import { OverviewView } from './OverviewView';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';

const navigateMock = vi.fn();

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}));

describe('OverviewView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders loading state initially', () => {
    wrap(<OverviewView />);
    expect(screen.getByText(/loading/)).toBeInTheDocument();
  });

  it('renders KPI strip after data loads', async () => {
    wrap(<OverviewView />);
    await waitFor(() =>
      expect(screen.getByRole('group', { name: 'KPI metrics' })).toBeInTheDocument(),
    );
    const strip = screen.getByRole('group', { name: 'KPI metrics' });
    expect(within(strip).getByText('pages')).toBeInTheDocument();
    expect(within(strip).getByText('sources')).toBeInTheDocument();
    expect(within(strip).getByText('lint issues')).toBeInTheDocument();
    expect(within(strip).getByText('last write')).toBeInTheDocument();
  });

  it('renders mount cards with names and roles', async () => {
    wrap(<OverviewView />);
    await waitFor(() => expect(screen.getAllByText('local').length).toBeGreaterThan(0));
    expect(screen.getAllByText('shared').length).toBeGreaterThan(0);
    expect(screen.getAllByText('platform').length).toBeGreaterThan(0);
  });

  it('renders the activity feed', async () => {
    wrap(<OverviewView />);
    await waitFor(() =>
      expect(screen.getByRole('log', { name: /recent writes/ })).toBeInTheDocument(),
    );
  });

  it('shows total page count from all mounts', async () => {
    wrap(<OverviewView />);
    // Mock mounts: 412 + 1180 + 342 + 184 = 2,118 pages
    await waitFor(() => expect(screen.getByText('2,118')).toBeInTheDocument());
  });

  it('shows "clean" when no lint issues', async () => {
    const noLintService: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        async listMounts() {
          return [
            {
              name: 'test',
              role: 'local',
              host: 'localhost',
              url: 'http://localhost',
              priority: 1,
              categories: null,
              status: 'healthy',
              pages: 5,
              sources: 2,
              lintIssues: 0,
              lastWrite: '2026-04-19T00:00:00Z',
              embedding: 'minilm',
              sizeKb: 100,
              desc: 'test mount',
            },
          ];
        },
      },
    };
    wrap(<OverviewView />, noLintService);
    await waitFor(() => expect(screen.getByText('clean')).toBeInTheDocument());
  });

  it('renders mount cards with aria-expanded=false initially', async () => {
    wrap(<OverviewView />);
    await waitFor(() =>
      expect(screen.getByRole('article', { name: 'mount local' })).toBeInTheDocument(),
    );
    const card = screen.getByRole('article', { name: 'mount local' });
    expect(card).toHaveAttribute('aria-expanded', 'false');
  });

  it('expands a mount card on click and shows detail', async () => {
    wrap(<OverviewView />);
    await waitFor(() =>
      expect(screen.getByRole('article', { name: 'mount local' })).toBeInTheDocument(),
    );
    const card = screen.getByRole('article', { name: 'mount local' });
    fireEvent.click(card);
    expect(card).toHaveAttribute('aria-expanded', 'true');
    // Expanded detail shows "recent activity" heading
    expect(within(card).getByText('recent activity')).toBeInTheDocument();
    // Shows role label (host is shown in collapsed portion, not repeated)
    expect(within(card).getByText('role')).toBeInTheDocument();
    // Shows size label
    expect(within(card).getByText('size')).toBeInTheDocument();
    // Does NOT show a duplicate host label in the expanded section
    expect(within(card).queryByText('host')).not.toBeInTheDocument();
  });

  it('collapses a mount card when clicked again', async () => {
    wrap(<OverviewView />);
    await waitFor(() =>
      expect(screen.getByRole('article', { name: 'mount local' })).toBeInTheDocument(),
    );
    const card = screen.getByRole('article', { name: 'mount local' });
    fireEvent.click(card);
    expect(card).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(card);
    expect(card).toHaveAttribute('aria-expanded', 'false');
    expect(within(card).queryByText('recent activity')).not.toBeInTheDocument();
  });

  it('shows categories in expanded mount detail when present', async () => {
    wrap(<OverviewView />);
    await waitFor(() =>
      expect(screen.getByRole('article', { name: 'mount platform' })).toBeInTheDocument(),
    );
    const card = screen.getByRole('article', { name: 'mount platform' });
    fireEvent.click(card);
    expect(within(card).getByText('categories')).toBeInTheDocument();
    expect(within(card).getByText('infra')).toBeInTheDocument();
    expect(within(card).getByText('api')).toBeInTheDocument();
    expect(within(card).getByText('arch')).toBeInTheDocument();
  });

  it('shows the warden count KPI without a roster section (wardens live on their tab)', async () => {
    wrap(<OverviewView />);
    await waitFor(() => expect(screen.getByText('wardens')).toBeInTheDocument());
    // The roster (cards with bios / pages-touched / last-dream) was removed:
    // it triplicated the Wardens tab and the context rail.
    expect(screen.queryByText('Wardens')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /open warden/i })).not.toBeInTheDocument();
  });

  it('shows the empty activity copy when the feed has no items', async () => {
    const noFeedService: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        getRecentWrites: async () => [],
      },
    };

    wrap(<OverviewView />, noFeedService);
    await waitFor(() => expect(screen.getByText('No recent activity.')).toBeInTheDocument());
  });

  it('uses singular copy when exactly one mount is connected', async () => {
    const singleMountService: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        async listMounts() {
          return [
            {
              name: 'solo',
              role: 'local',
              host: 'localhost',
              url: 'http://localhost',
              priority: 1,
              categories: null,
              status: 'healthy',
              pages: 5,
              sources: 2,
              lintIssues: 0,
              lastWrite: '2026-04-19T00:00:00Z',
              embedding: 'minilm',
              sizeKb: 100,
              desc: 'solo mount',
            },
          ];
        },
      },
    };

    wrap(<OverviewView />, singleMountService);
    await waitFor(() => expect(screen.getByText(/1 instance connected/i)).toBeInTheDocument());
  });

  it('shows an em dash when no mount has a last-write timestamp', async () => {
    const noLastWriteService: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        async listMounts() {
          return [
            {
              name: 'empty',
              role: 'local',
              host: 'localhost',
              url: 'http://localhost',
              priority: 1,
              categories: null,
              status: 'healthy',
              pages: 1,
              sources: 1,
              lintIssues: 0,
              lastWrite: '',
              embedding: 'minilm',
              sizeKb: 10,
              desc: 'empty timestamp mount',
            },
          ];
        },
      },
    };

    wrap(<OverviewView />, noLastWriteService);
    await waitFor(() => expect(screen.getByText('last write')).toBeInTheDocument());
    const lastWriteCard = screen.getByText('last write').closest('.niuu-kpi-card');
    expect(lastWriteCard).not.toBeNull();
    expect(within(lastWriteCard as HTMLElement).getByText('—')).toBeInTheDocument();
  });

  it('uses the fallback styling path for unknown activity kinds', async () => {
    const unknownKindService: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        getRecentWrites: async () => [
          {
            id: 'recent-unknown',
            timestamp: '2026-04-19T00:00:00Z',
            kind: 'unknown-kind',
            mount: 'local',
            ravn: 'ravn-fjolnir',
            message: 'manual annotation added',
          },
        ],
      },
    };

    wrap(<OverviewView />, unknownKindService);
    await waitFor(() => expect(screen.getByText('unknown-kind')).toBeInTheDocument());
    expect(screen.getByText('unknown-kind').className).toContain('niuu:text-text-secondary');
  });

  it('renders error banner on mount fetch failure', async () => {
    const failService: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        async listMounts() {
          throw new Error('network error');
        },
      },
    };
    wrap(<OverviewView />, failService);
    await waitFor(() => expect(screen.getByText('network error')).toBeInTheDocument());
  });
});
