import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { VolundrSessionRoute, VolundrArchivedRoute, VolundrSessionsRoute } from './routes';
import {
  createMockVolundrService,
  createMockSessionStore,
  createMockMetricsStream,
} from '../adapters/mock';
import type { IPtyStream } from '../ports/IPtyStream';
import type { IFileSystemPort } from '../ports/IFileSystemPort';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('@xterm/xterm', () => ({
  Terminal: class MockXtermTerminal {
    open = vi.fn();
    write = vi.fn();
    dispose = vi.fn();
    loadAddon = vi.fn();
    onData = vi.fn().mockReturnValue({ dispose: vi.fn() });
    options = {};
  },
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class MockFitAddon {
    fit = vi.fn();
    dispose = vi.fn();
  },
}));

vi.mock('shiki', () => ({
  codeToHtml: vi.fn().mockResolvedValue('<pre><code>highlighted</code></pre>'),
}));

class ResizeObserverStub {
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub);

// TanStack Router: stub useParams so route components work outside a router.
const routeState = vi.hoisted(() => ({
  search: {} as { instance_id?: unknown; returnTo?: unknown },
  navigate: vi.fn(),
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => routeState.navigate,
  useParams: vi.fn().mockReturnValue({ sessionId: 'sess-route-test' }),
  useSearch: () => routeState.search,
  Link: ({ children, to, ...rest }: { children?: React.ReactNode; to?: string }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

const uiModeState = vi.hoisted(() => ({ mode: 'advanced' as 'simple' | 'advanced' }));

vi.mock('@niuulabs/shell', () => ({
  useUiMode: () => uiModeState.mode,
}));

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function buildPtyStream(): IPtyStream {
  return {
    subscribe: vi.fn().mockReturnValue(() => {}),
    send: vi.fn(),
  };
}

function buildFilesystem(): IFileSystemPort {
  return {
    listTree: vi.fn().mockResolvedValue([]),
    expandDirectory: vi.fn().mockResolvedValue([]),
    readFile: vi.fn().mockResolvedValue(''),
  };
}

function wrap(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          bifrost: createMockBifrostService(),
          volundr: createMockVolundrService(),
          ptyStream: buildPtyStream(),
          filesystem: buildFilesystem(),
          sessionStore: createMockSessionStore(),
          metricsStream: createMockMetricsStream(),
        }}
      >
        {ui}
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  localStorage.clear();
  routeState.search = {};
  routeState.navigate.mockReset();
  uiModeState.mode = 'advanced';
});

describe('VolundrSessionsRoute', () => {
  it('renders the full forge console in advanced mode', () => {
    uiModeState.mode = 'advanced';
    wrap(<VolundrSessionsRoute />);
    expect(screen.getByTestId('sessions-page')).toBeInTheDocument();
  });

  it('renders the calm simple list in simple mode', () => {
    uiModeState.mode = 'simple';
    wrap(<VolundrSessionsRoute />);
    expect(screen.getByTestId('simple-sessions-page')).toBeInTheDocument();
  });
});

describe('VolundrSessionRoute', () => {
  it('renders the session page with the param sessionId', async () => {
    localStorage.setItem('niuu.forge.details', '1');
    wrap(<VolundrSessionRoute />);
    const label = await screen.findByTestId('session-id-label');
    expect(label).toHaveTextContent('sess-rou');
  });

  it('renders in interactive (non-read-only) mode', async () => {
    wrap(<VolundrSessionRoute />);
    await screen.findByTestId('live-session-detail-page');
    expect(screen.queryByText('Archived')).not.toBeInTheDocument();
  });

  it('consumes a source-qualified Work return route', async () => {
    routeState.search = {
      instance_id: 'forge-east',
      returnTo: '/ting/work/campaign%3Acampaign-1',
    };
    wrap(<VolundrSessionRoute />);

    const back = await screen.findByRole('button', { name: 'Back to Work' });
    fireEvent.click(back);
    expect(routeState.navigate).toHaveBeenCalledWith({
      to: '/ting/work/campaign%3Acampaign-1',
    });
  });

  it('does not render navigation for an external return target', async () => {
    routeState.search = {
      instance_id: 'forge-east',
      returnTo: 'https://malicious.example/ting/work/campaign-1',
    };
    wrap(<VolundrSessionRoute />);

    await screen.findByTestId('live-session-detail-page');
    expect(screen.queryByRole('button', { name: 'Back to Work' })).not.toBeInTheDocument();
  });
});

describe('VolundrArchivedRoute', () => {
  it('renders the session page with the param sessionId', async () => {
    localStorage.setItem('niuu.forge.details', '1');
    wrap(<VolundrArchivedRoute />);
    const label = await screen.findByTestId('session-id-label');
    expect(label).toHaveTextContent('sess-rou');
  });

  it('renders with the archived (read-only) badge', async () => {
    wrap(<VolundrArchivedRoute />);
    await waitFor(() => {
      expect(screen.getByText('Archived')).toBeInTheDocument();
    });
  });

  it('consumes a source-qualified Work return route', async () => {
    routeState.search = {
      instance_id: 'forge-east',
      returnTo: '/ting/work/campaign%3Acampaign-1',
    };
    wrap(<VolundrArchivedRoute />);

    const back = await screen.findByRole('button', { name: 'Back to Work' });
    fireEvent.click(back);
    expect(routeState.navigate).toHaveBeenCalledWith({
      to: '/ting/work/campaign%3Acampaign-1',
    });
  });

  it('does not render navigation for an external return target', async () => {
    routeState.search = {
      instance_id: 'forge-east',
      returnTo: 'https://malicious.example/ting/work/campaign-1',
    };
    wrap(<VolundrArchivedRoute />);

    await screen.findByTestId('live-session-detail-page');
    expect(screen.queryByRole('button', { name: 'Back to Work' })).not.toBeInTheDocument();
  });
});
