import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { VolundrSessionPage } from './VolundrSessionPage';
import type { IPtyStream } from '../ports/IPtyStream';
import type { IFileSystemPort, FileTreeNode } from '../ports/IFileSystemPort';

// ---------------------------------------------------------------------------
// Mock xterm (no Canvas in jsdom)
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MOCK_TREE: FileTreeNode[] = [
  { name: 'index.ts', path: '/workspace/index.ts', kind: 'file', size: 256 },
  {
    name: 'src',
    path: '/workspace/src',
    kind: 'directory',
    children: [{ name: 'app.ts', path: '/workspace/src/app.ts', kind: 'file', size: 512 }],
  },
];

function buildPtyStream(overrides?: Partial<IPtyStream>): IPtyStream {
  return {
    subscribe: vi.fn().mockReturnValue(() => {}),
    send: vi.fn(),
    ...overrides,
  };
}

function buildFilesystem(overrides?: Partial<IFileSystemPort>): IFileSystemPort {
  return {
    listTree: vi.fn().mockResolvedValue(MOCK_TREE),
    expandDirectory: vi.fn().mockResolvedValue([]),
    readFile: vi.fn().mockResolvedValue('// file content\n'),
    ...overrides,
  };
}

function wrap(ui: React.ReactNode, ptyStream = buildPtyStream(), filesystem = buildFilesystem()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ ptyStream, filesystem }}>{ui}</ServicesProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('VolundrSessionPage', () => {
  it('renders the session id label', () => {
    wrap(<VolundrSessionPage sessionId="sess-abc" />);
    expect(screen.getByTestId('session-id-label')).toHaveTextContent('sess-abc');
  });

  it('shows the "archived" badge in readOnly mode', () => {
    wrap(<VolundrSessionPage sessionId="sess-abc" readOnly />);
    expect(screen.getByText('archived')).toBeInTheDocument();
  });

  it('does NOT show the "archived" badge in interactive mode', () => {
    wrap(<VolundrSessionPage sessionId="sess-abc" />);
    expect(screen.queryByText('archived')).not.toBeInTheDocument();
  });

  it('renders the terminal container', async () => {
    wrap(<VolundrSessionPage sessionId="sess-abc" />);
    await waitFor(() => expect(screen.getByTestId('terminal-container')).toBeInTheDocument());
  });

  it('passes readOnly to Terminal in archived mode', async () => {
    wrap(<VolundrSessionPage sessionId="sess-abc" readOnly />);
    await waitFor(() => expect(screen.getByTestId('terminal-readonly-badge')).toBeInTheDocument());
  });

  it('fetches and renders the file tree', async () => {
    const filesystem = buildFilesystem();
    wrap(<VolundrSessionPage sessionId="sess-abc" />, buildPtyStream(), filesystem);
    await waitFor(() => expect(screen.getByTestId('filetree-root')).toBeInTheDocument());
    expect(screen.getByText('index.ts')).toBeInTheDocument();
  });

  it('shows loading state before file tree resolves', () => {
    const slowFs = buildFilesystem({
      listTree: vi.fn().mockReturnValue(new Promise(() => {})),
    });
    wrap(<VolundrSessionPage sessionId="sess-abc" />, buildPtyStream(), slowFs);
    expect(screen.getByTestId('filetree-loading')).toBeInTheDocument();
  });

  it('shows error state when file tree fails', async () => {
    const errorFs = buildFilesystem({
      listTree: vi.fn().mockRejectedValue(new Error('network error')),
    });
    wrap(<VolundrSessionPage sessionId="sess-abc" />, buildPtyStream(), errorFs);
    await waitFor(() => expect(screen.getByTestId('filetree-error')).toBeInTheDocument());
  });

  it('shows placeholder when no file is selected', async () => {
    wrap(<VolundrSessionPage sessionId="sess-abc" />);
    await waitFor(() => expect(screen.getByTestId('file-viewer-placeholder')).toBeInTheDocument());
  });

  it('opens file viewer when a file is clicked', async () => {
    const filesystem = buildFilesystem();
    wrap(<VolundrSessionPage sessionId="sess-abc" />, buildPtyStream(), filesystem);
    await waitFor(() => expect(screen.getByText('index.ts')).toBeInTheDocument());

    fireEvent.click(screen.getByText('index.ts'));

    await waitFor(() => expect(screen.getByTestId('file-viewer')).toBeInTheDocument());
    expect(screen.getByTestId('file-viewer-path')).toHaveTextContent('index.ts');
  });

  it('shows error in file viewer when readFile throws', async () => {
    const filesystem = buildFilesystem({
      readFile: vi.fn().mockRejectedValue(new Error('permission denied')),
    });
    wrap(<VolundrSessionPage sessionId="sess-abc" />, buildPtyStream(), filesystem);
    await waitFor(() => expect(screen.getByText('index.ts')).toBeInTheDocument());

    fireEvent.click(screen.getByText('index.ts'));

    await waitFor(() =>
      expect(screen.getByTestId('file-viewer-error')).toHaveTextContent('permission denied'),
    );
  });

  it('closes file viewer when close button is clicked', async () => {
    const filesystem = buildFilesystem();
    wrap(<VolundrSessionPage sessionId="sess-abc" />, buildPtyStream(), filesystem);
    await waitFor(() => expect(screen.getByText('index.ts')).toBeInTheDocument());

    fireEvent.click(screen.getByText('index.ts'));
    await waitFor(() => expect(screen.getByTestId('file-viewer')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('file-viewer-close'));
    expect(screen.getByTestId('file-viewer-placeholder')).toBeInTheDocument();
  });

  it('subscribes to PTY stream with the correct session id', async () => {
    const ptyStream = buildPtyStream();
    wrap(<VolundrSessionPage sessionId="sess-xyz" />, ptyStream);
    await waitFor(() =>
      expect(ptyStream.subscribe).toHaveBeenCalledWith('sess-xyz', expect.any(Function)),
    );
  });
});
