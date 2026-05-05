import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { VolundrSessionRoute, VolundrArchivedRoute } from './routes';
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
  Terminal: vi.fn().mockImplementation(() => ({
    open: vi.fn(),
    write: vi.fn(),
    dispose: vi.fn(),
    loadAddon: vi.fn(),
    onData: vi.fn().mockReturnValue({ dispose: vi.fn() }),
    options: {},
  })),
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: vi.fn().mockImplementation(() => ({
    fit: vi.fn(),
    dispose: vi.fn(),
  })),
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
vi.mock('@tanstack/react-router', () => ({
  useParams: vi.fn().mockReturnValue({ sessionId: 'sess-route-test' }),
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

describe('VolundrSessionRoute', () => {
  it('renders the session page with the param sessionId', async () => {
    wrap(<VolundrSessionRoute />);
    const label = await screen.findByTestId('session-id-label');
    expect(label).toHaveTextContent('sess-rou');
  });

  it('renders in interactive (non-read-only) mode', async () => {
    wrap(<VolundrSessionRoute />);
    await screen.findByTestId('live-session-detail-page');
    expect(screen.queryByText('Archived')).not.toBeInTheDocument();
  });
});

describe('VolundrArchivedRoute', () => {
  it('renders the session page with the param sessionId', async () => {
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
});
